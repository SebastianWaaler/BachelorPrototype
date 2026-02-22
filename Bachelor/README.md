første gang etter pull i terminalen:
  cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install flask flask-cors

i cd backend: 
    python app.py
backend kjøres på http://127.0.0.1:5000

i cd frontend:
    python -m http.server 5173
åpne browser med link: http://127.0.0.1:5173
