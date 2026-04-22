import requests

url = "https://api.cal.com/v2/event-types"

headers = {
    "Authorization": "cal_live_cac0f10ba745edc3bc6f41d447708469",
    "cal-api-version": "2024-06-14"
}

response = requests.get(url, headers=headers)

print(response.json())