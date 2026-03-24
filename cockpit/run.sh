#!/bin/bash
echo "============================================"
echo " Finance Routine Cockpit - Starting..."
echo "============================================"
cd "$(dirname "$0")"
pip install -r requirements.txt --quiet
streamlit run app.py --server.port 8501 --server.headless false
