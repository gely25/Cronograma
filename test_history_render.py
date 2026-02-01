import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestion_activos.settings')
django.setup()
from django.test import Client

client = Client()
response = client.get('/notifications/dashboard/')
if response.status_code == 200:
    html = response.content.decode('utf-8')
    start = html.find('id="main-content-history"')
    if start != -1:
        # Look for table rows
        rows_start = html.find('<tbody', start)
        rows_end = html.find('</tbody', rows_start)
        tbody_html = html[rows_start:rows_end+8]
        print("TBODY CONTENT:")
        print(tbody_html)
        print("\nROW COUNT in HTML:", tbody_html.count('<tr'))
    else:
        print("main-content-history NOT FOUND")
else:
    print(f"Error: {response.status_code}")
