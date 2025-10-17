import os
import requests

from bs4 import BeautifulSoup
import pandas as pd

bucket_name = os.getenv("BUCKET_NAME")

month_dict = {
    "January": "01",
    "February": "02",
    "March": "03",
    "April": "04",
    "May": "05",
    "June": "06",
    "July": "07",
    "August": "08",
    "September": "09",
    "October": "10",
    "November": "11",
    "December": "12"
}

response = requests.get('https://www.bankrate.com/mortgages/mortgage-rates/?mortgageType=Purchase&partnerId=br3&pid=br3&pointsChanged=false&purchaseDownPayment=224000&purchaseLoanTerms=30yr%2C5-1arm%2C5-6arm&purchasePoints=All&purchasePrice=1120000&purchasePropertyType=SingleFamily&purchasePropertyUse=PrimaryResidence&searchChanged=false&ttcid&userCreditScore=780&userDebtToIncomeRatio=0&userFha=false&userVeteranStatus=NoMilitaryService&zipCode=59802')
soup = BeautifulSoup(response.text, 'html.parser')

span = soup.find('span', attrs={'data-sheets-root': '1'})
date = span.text.strip()
date_str = date[date.find(',') +2 : ]

month, day_year = date_str.split(" ", 1)
day = day_year.split(",")[0].zfill(2)  
year = day_year.split(",")[1].strip() 
formatted_date = f"{year}-{month_dict[month]}-{day}"

after_text = span.next_sibling.strip()
start_find = after_text.find('the national average 30-year fixed mortgage APR is')
end_find = after_text.find('%', start_find + 50)
mortgate_rate = after_text[start_find + 50 :end_find]

mortgage_rate_df = pd.DataFrame({"date": [formatted_date], 'rate': [mortgate_rate]})
existing_rates_df = pd.read_csv(f"gs://{bucket_name}/mortgage_rates.csv")
final_mortgage_rate_df = pd.concat([existing_rates_df, mortgage_rate_df])
                                    
final_mortgage_rate_df.to_csv(f"gs://{bucket_name}/mortgage_rates.csv", storage_options={"token": None})
