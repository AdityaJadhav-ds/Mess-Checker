# Project: Smart Mess Checker (Streamlit)
# Single-file scaffold + helper modules represented as file blocks.

### FILE: requirements.txt
streamlit
pymongo[srv]
pydantic
pandas
openpyxl
pytest
pytest-asyncio
python-dotenv
streamlit-aggrid

### FILE: README.md
# Smart Mess Checker — Streamlit Starter

# This scaffold provides a minimal but production-minded starter for the Smart Mess Checker app.

Run locally:
1. Create `.env` with `MONGODB_URI` and `MONGODB_DB`.
2. `pip install -r requirements.txt`
3. `streamlit run app.py`

Includes:
- Streamlit frontend (app.py)
- MongoDB atomic `add_tiffin` implementation using transactions
- Undo for last X minutes
- Simple exports
- Seed script
- Example concurrency test

### FILE: .env.example
MONGODB_URI=mongodb+srv://<user>:<pass>@cluster0.mongodb.net
MONGODB_DB=smart_mess
UNDO_WINDOW_MINUTES=5

### FILE: streamlit_app/services/db.py
from pymongo import MongoClient
from functools import lru_cache
import os
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv('MONGODB_URI')
MONGODB_DB = os.getenv('MONGODB_DB', 'smart_mess')

@lru_cache(maxsize=1)
def get_db_client():
    if not MONGODB_URI:
        raise RuntimeError('MONGODB_URI not set in environment')
    client = MongoClient(MONGODB_URI)
    return client


def get_db():
    client = get_db_client()
    return client[MONGODB_DB]

### FILE: streamlit_app/models/schemas.py
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class Cycle(BaseModel):
    start_date: datetime
    end_date: Optional[datetime] = None
    tiffins_taken: int = 0
    status: str = "Active"

class CycleHistory(BaseModel):
    start_date: datetime
    end_date: datetime
    tiffins_taken: int
    day_count: int
    night_count: int
    amount: float
    status: str
    payment_date: Optional[datetime] = None

class TiffinLog(BaseModel):
    customer_id: str
    date: datetime
    slot: str
    timestamp: datetime
    synced: bool = True

class Customer(BaseModel):
    name: str
    phone: str
    address: Optional[str] = None
    start_date: datetime
    price_per_tiffin: float = 0.0
    cycle_limit: int = 30
    current_cycle: Cycle
    cycle_history: List[CycleHistory] = []

### FILE: streamlit_app/services/tiffin_service.py
from datetime import datetime, timedelta
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from .db import get_db, get_db_client
import os

UNDO_WINDOW = int(os.getenv('UNDO_WINDOW_MINUTES', '5'))

DB = get_db()

# Ensure unique index on (customer_id, date, slot)
DB.logs.create_index([('customer_id', 1), ('date', 1), ('slot', 1)], unique=True)


def add_tiffin(customer_id: str, date: datetime.date, slot: str, operator: str = 'staff'):
    """Atomically add a tiffin log for given slot. Raises if duplicate."""
    client = get_db_client()
    db = client[DB.name]
    now = datetime.utcnow()
    # normalize date to midnight UTC
    date_only = datetime(date.year, date.month, date.day)

    with client.start_session() as session:
        def txn(s):
            logs = db.logs
            customers = db.customers
            # duplicate check
            if logs.find_one({'customer_id': ObjectId(customer_id), 'date': date_only, 'slot': slot}, session=s):
                raise DuplicateKeyError('Tiffin already logged for this slot')
            logs.insert_one({'customer_id': ObjectId(customer_id), 'date': date_only, 'slot': slot, 'timestamp': now, 'operator': operator}, session=s)
            # increment current_cycle.tiffins_taken
            updated = customers.find_one_and_update(
                {'_id': ObjectId(customer_id)},
                {'$inc': {'current_cycle.tiffins_taken': 1}},
                return_document=True,
                session=s
            )
            if not updated:
                raise RuntimeError('Customer not found')

            # rollover logic
            if updated['current_cycle']['tiffins_taken'] >= updated.get('cycle_limit', 30):
                end = now
                start_date = updated['current_cycle']['start_date']
                # count day/night during cycle window
                day_count = logs.count_documents({'customer_id': ObjectId(customer_id), 'slot': 'day', 'date': {'$gte': start_date, '$lte': end}}, session=s)
                night_count = logs.count_documents({'customer_id': ObjectId(customer_id), 'slot': 'night', 'date': {'$gte': start_date, '$lte': end}}, session=s)
                history_entry = {
                    'start_date': start_date,
                    'end_date': end,
                    'tiffins_taken': updated['current_cycle']['tiffins_taken'],
                    'day_count': day_count,
                    'night_count': night_count,
                    'amount': updated['current_cycle']['tiffins_taken'] * updated.get('price_per_tiffin', 0),
                    'status': 'Unpaid'
                }
                customers.update_one({'_id': ObjectId(customer_id)}, {'$push': {'cycle_history': history_entry}, '$set': {'current_cycle': {'start_date': datetime(end.year, end.month, end.day) + timedelta(days=1), 'tiffins_taken': 0, 'status': 'Active'}}}, session=s)

        session.with_transaction(txn)
    return {'status': 'ok', 'timestamp': now}


def undo_last_tiffin(customer_id: str):
    """Undo last tiffin for customer within UNDO_WINDOW minutes."""
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=UNDO_WINDOW)
    logs = DB.logs
    customers = DB.customers
    last = logs.find_one({'customer_id': ObjectId(customer_id)}, sort=[('timestamp', -1)])
    if not last:
        raise RuntimeError('No logs to undo')
    if last['timestamp'] < cutoff:
        raise RuntimeError('Undo window expired')
    client = get_db_client()
    with client.start_session() as session:
        def txn(s):
            res = logs.delete_one({'_id': last['_id']}, session=s)
            if res.deleted_count == 0:
                raise RuntimeError('Failed to delete')
            customers.update_one({'_id': ObjectId(customer_id)}, {'$inc': {'current_cycle.tiffins_taken': -1}}, session=s)
        session.with_transaction(txn)
    return {'status': 'ok', 'removed': str(last['_id'])}


def get_reports(slot: str = None, start: datetime = None, end: datetime = None):
    q = {}
    if slot in ('day', 'night'):
        q['slot'] = slot
    if start and end:
        q['date'] = {'$gte': start, '$lte': end}
    cursor = DB.logs.find(q).sort([('date', -1)])
    return list(cursor)

### FILE: app.py
import streamlit as st
from datetime import date, datetime
from bson import ObjectId
import pandas as pd

from streamlit_app.services.db import get_db
from streamlit_app.services.tiffin_service import add_tiffin, undo_last_tiffin, get_reports

st.set_page_config(page_title='Smart Mess Checker', layout='wide')

DB = get_db()

st.title('Smart Mess Checker — Streamlit')

col1, col2 = st.columns([2,1])
with col1:
    st.header('Quick Add')
    cust = st.selectbox('Customer', options=[str(c['_id']) + ' | ' + c.get('name','') for c in DB.customers.find()])
    if cust:
        cust_id = cust.split('|')[0].strip()
    else:
        cust_id = None
    chosen_date = st.date_input('Date', value=date.today())
    slot = st.radio('Slot', options=['day','night'], horizontal=True)
    if st.button('Add Tiffin'):
        if not cust_id:
            st.error('Select customer')
        else:
            try:
                res = add_tiffin(cust_id, chosen_date, slot)
                st.success(f'Added {slot} for {chosen_date} at {res["timestamp"]}')
            except Exception as e:
                st.error(str(e))

    if st.button('Undo Last'):
        if not cust_id:
            st.error('Select customer')
        else:
            try:
                res = undo_last_tiffin(cust_id)
                st.success('Undo successful')
            except Exception as e:
                st.error(str(e))

with col2:
    st.header('Reports')
    slot_filter = st.selectbox('Slot Filter', options=['both','day','night'])
    start = st.date_input('Start', value=date.today())
    end = st.date_input('End', value=date.today())
    if st.button('Refresh Report'):
        s = None if slot_filter=='both' else slot_filter
        rows = get_reports(s, datetime(start.year,start.month,start.day), datetime(end.year,end.month,end.day))
        if not rows:
            st.info('No rows')
        else:
            df = pd.DataFrame(rows)
            df['date'] = pd.to_datetime(df['date']).dt.date
            st.dataframe(df)
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button('Download CSV', csv, file_name='report.csv', mime='text/csv')

st.sidebar.header('Actions')
if st.sidebar.button('Seed Example Data'):
    import importlib
    import scripts.seed_data as sd
    sd.seed()
    st.success('Seeded')

### FILE: scripts/seed_data.py
from datetime import datetime, timedelta
from bson import ObjectId
from streamlit_app.services.db import get_db

DB = get_db()

def seed():
    DB.customers.delete_many({})
    DB.logs.delete_many({})
    c = {
        '_id': ObjectId(),
        'name': 'Ramesh',
        'phone': '9999999999',
        'start_date': datetime.utcnow(),
        'price_per_tiffin': 50,
        'cycle_limit': 5,
        'current_cycle': {'start_date': datetime.utcnow(), 'tiffins_taken': 0, 'status': 'Active'},
        'cycle_history': []
    }
    DB.customers.insert_one(c)
    # add a couple of logs
    DB.logs.insert_one({'customer_id': c['_id'], 'date': datetime.utcnow(), 'slot': 'day', 'timestamp': datetime.utcnow()})
    print('Seeded customer id:', str(c['_id']))

if __name__ == '__main__':
    seed()

### FILE: tests/test_rollover.py
import pytest
from datetime import datetime, date
from streamlit_app.services.tiffin_service import add_tiffin
from streamlit_app.services.db import get_db
from bson import ObjectId

DB = get_db()

@pytest.mark.asyncio
async def test_concurrent_adds(monkeypatch):
    # This is a conceptual test: in CI you'd spawn threads/processes to hit add_tiffin concurrently
    # For now ensure add_tiffin increments and prevents duplicates.
    DB.customers.delete_many({})
    DB.logs.delete_many({})
    cid = ObjectId()
    DB.customers.insert_one({'_id': cid, 'name': 'Test', 'price_per_tiffin': 10, 'cycle_limit': 2, 'current_cycle': {'start_date': datetime.utcnow(), 'tiffins_taken': 0, 'status': 'Active'}, 'cycle_history': []})
    # add day
    res = add_tiffin(str(cid), date.today(), 'day')
    assert res['status'] == 'ok'
    # adding same slot again should raise
    with pytest.raises(Exception):
        add_tiffin(str(cid), date.today(), 'day')

### FILE: .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: python -m pip install --upgrade pip
      - run: pip install -r requirements.txt
      - run: pytest -q

### FILE: notes.md
- This scaffold is intentionally minimal. For production:
  - Add proper authentication (Supabase Auth or custom JWT)
  - Add role checks for Admin/Staff
  - Harden indexes and TTLs
  - Add monitoring and backup plan for MongoDB

# End of scaffold
