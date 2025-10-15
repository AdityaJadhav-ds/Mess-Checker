# app.py
"""
Smart Mess Checker — Streamlit
Production-ready version
"""

import streamlit as st
from datetime import date, datetime
from bson import ObjectId
import pandas as pd

from streamlit_app.services.db import get_db
from streamlit_app.services.tiffin_service import add_tiffin, undo_last_tiffin, get_reports

st.set_page_config(page_title='Smart Mess Checker', layout='wide')

DB = get_db()

st.title('🍽️ Smart Mess Checker — Streamlit')

# --- Layout ---
col1, col2 = st.columns([2,1])

# --- Quick Add Section ---
with col1:
    st.header('Quick Add')
    customers_list = [f"{str(c['_id'])} | {c.get('name','')}" for c in DB.customers.find()]
    cust = st.selectbox('Customer', options=customers_list)
    cust_id = cust.split('|')[0].strip() if cust else None

    chosen_date = st.date_input('Date', value=date.today())
    slot = st.radio('Slot', options=['day','night'], horizontal=True)

    if st.button('Add Tiffin'):
        if not cust_id:
            st.error('Please select a customer.')
        else:
            try:
                dt = datetime.combine(chosen_date, datetime.min.time())
                res = add_tiffin(cust_id, dt, slot)
                st.success(f'Added {slot} for {chosen_date} at {res["timestamp"]}')
            except Exception as e:
                st.error(f"Error: {str(e)}")

    if st.button('Undo Last'):
        if not cust_id:
            st.error('Please select a customer.')
        else:
            try:
                res = undo_last_tiffin(cust_id)
                st.success(f'Undo successful: removed log {res["removed"]}')
            except Exception as e:
                st.error(f"Error: {str(e)}")

# --- Reports Section ---
with col2:
    st.header('Reports')
    slot_filter = st.selectbox('Slot Filter', options=['both','day','night'])
    start = st.date_input('Start', value=date.today())
    end = st.date_input('End', value=date.today())
    if st.button('Refresh Report'):
        s = None if slot_filter=='both' else slot_filter
        rows = get_reports(s, datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.max.time()))
        if not rows:
            st.info('No rows found for selected filters.')
        else:
            df = pd.DataFrame(rows)
            df['date'] = pd.to_datetime(df['date']).dt.date
            st.dataframe(df)
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button('Download CSV', csv, file_name='report.csv', mime='text/csv')

# --- Sidebar Actions ---
st.sidebar.header('Actions')
if st.sidebar.button('Seed Example Data'):
    import importlib
    import scripts.seed_data as sd
    importlib.reload(sd)
    sd.seed()
    st.success('Seeded example customer and logs!')
