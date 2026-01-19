import requests

try:
    response = requests.get('http://127.0.0.1:5000/register')
    print(f'/register status code: {response.status_code}')
    if response.status_code == 200:
        print("Register page is accessible.")
    else:
        print("Register page returned unexpected status.")
        
    response = requests.get('http://127.0.0.1:5000/login')
    print(f'/login status code: {response.status_code}')

except Exception as e:
    print(f"Error accessing server: {e}")
