# pharmacy_management.py

import streamlit as st
import pandas as pd

# Sample data - would normally come from database
facilities_data = {
    'Name': ['Facility A', 'Facility B', 'Facility C'],
    'Schedule': ['Daily', 'Weekly', 'Bi-Weekly'],
    'Contact Info': ['contactA@example.com', 'contactB@example.com', 'contactC@example.com'],
    'Status': ['Active', 'Pending', 'Inactive']
}

def facility_directory():
    st.title("Pharmacy Management")
    st.markdown("""
    <style>
    .facility-table {
        border-collapse: collapse;
        width: 100%;
        margin: 1em 0;
    }
    .facility-table th, .facility-table td {
        border: 1px solid #dbe4f0;
        padding: 12px;
        text-align: left;
    }
    .facility-table th {
        background-color: #f8fbff;
    }
    </style>
    """, unsafe_allow_html=True)

    df = pd.DataFrame(facilities_data)
    st.markdown(df.to_html(classes='facility-table'), unsafe_allow_html=True)

    st.markdown("### Add New Facility")
    with st.form("add_facility_form"):
        name = st.text_input("Facility Name")
        schedule = st.selectbox("Schedule Type", ["Daily", "Weekly", "Bi-Weekly"])
        contact = st.text_input("Contact Info")
        submitted = st.form_submit_button("Add Facility")
        if submitted:
            # Add to database logic here
            st.success(f"Added {name} to directory")

if __name__ == "__main__":
    facility_directory()