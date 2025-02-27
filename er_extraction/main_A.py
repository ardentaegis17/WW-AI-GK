import json
import requests 
import sqlite3
import pandas as pd
import re
import os

from bs4 import BeautifulSoup
from googlesearch import search
from yahooquery import Ticker
import pycountry_convert as pc
import random
import warnings
warnings.filterwarnings('ignore')

TOKEN = os.getenv("DIFFBOT_KEY")
FIELDS = "entities,facts"
HOST = "nl.diffbot.com"

def get_request(payload):
  '''
  Sends a POST request to the API with the given payload, retrieves JSON data from the response, and handles errors gracefully.

    Parameters:

    payload (dict): Dictionary containing the request data, including text content, language, and format.
    Returns:

    dict: Parsed JSON response from the API if successful; otherwise, prints error information and returns None.
  '''
  res = requests.post("https://{}/v1/?fields={}&token={}".format(HOST, FIELDS, TOKEN), json=payload)
  ret = None
  try:
    ret = res.json()
  except:
    print("Bad response: " + res.text)
    print(res.status_code)
    print(res.headers)
  return ret

def extract_entites(res):
    '''
    Extracts entities from the API response and filters them by salience. Adds a label to classify entities as company, industry, country, location, or product.

    Parameters:

    res (dict): The JSON response from the API containing entities data.
    Returns:

    DataFrame: Filtered entities with columns name, salience, and Labels.
    '''
    ents = pd.DataFrame.from_dict(res["entities"])
    if not ents.empty:
        salient_ents = ents[ents["salience"] > 0.5]
        salient_ents["Labels"] = None
        for i, row in salient_ents.iterrows():
            if len(row['allTypes']) != 0:
                names = [ent_type["name"] for ent_type in row['allTypes']]
                if "organization" in names:
                    salient_ents.loc[i,"Labels"] = 'company'
                elif ("field of work" in names)  or ("industry" in names) or ("industry" in row['name']):
                    salient_ents.loc[i,'Labels'] = 'industry'
                elif "country" in names:
                    salient_ents.loc[i,'Labels'] = 'country'
                elif "location" in names:
                    salient_ents.loc[i,'Labels'] = 'location'
                elif "product" in names:
                    salient_ents.loc[i,'Labels'] = 'product'
                else:
                    salient_ents.loc[i,'Labels'] = row['allTypes'][0]['name']
                

        fin_ents = salient_ents[['name','salience','Labels']]
        return fin_ents
    return ents

def extract_relationships(res):
    '''
    Description:
    Processes the API response to extract relationships. Adds columns for entity names, properties, values, and any relevant evidence.

    Parameters:

    res (dict): JSON response from the API containing relationship data.
    Returns:

    DataFrame: Filtered relationships with columns entity, property, value, and evidence.

    '''
    rels =  pd.DataFrame.from_dict(res["facts"])
    if not rels.empty:
        for i, row in rels.iterrows():
            rels.loc[i,"entity"] = row["entity"]["name"]
            rels.loc[i,"property"] = row["property"]["name"]
            rels.loc[i,"value"] = row["value"]["name"]
            if row["evidence"] != []:
                rels.loc[i,"evidence"] = row["evidence"][0].get("passage",None)
        fin_rels = rels[['entity','property','value','evidence']]
        return fin_rels
    return rels



def get_company_ticker(self):
    '''
    Description:
    Retrieves the ticker symbol of a company from a Yahoo Finance search result.

    Parameters:

    self (str): Company name.
    Returns:

    str: The extracted ticker symbol from Yahoo Finance.

    '''
    searchval = 'yahoo finance '+ self
    link = []
    #limits to the first link
    for url in search(searchval, stop = 1):
        link.append(url)

    link = str(link[0])
    link=link.split("/")
    if link[-1]=='':
        ticker=link[-2]
    else:
        x=link[-1].split('=')
        ticker=x[-1]
    print(ticker)
    return(ticker)


def get_company_name(ticker):
    
    '''
    Fetches the company's full name using the ticker symbol by querying Yahoo Finance.

    Parameters:

    ticker (str): Company ticker symbol.
    Returns:

    str: Full company name if found, else returns the ticker symbol.

    '''

    try:
        ticker_info = Ticker(ticker)
        company_name = ticker_info.quote_type[ticker]['longName']
        print(f"Found Company: {company_name}")
        return company_name
    except Exception as e:
        print(f"Error fetching company name for ticker {ticker}: {e}")
        return ticker

def get_wikipedia_article(company_name_or_ticker):
    
    '''
    Parameters:

    company_name_or_ticker (str): The name or ticker symbol of the company.
    Returns:

    str: Combined title and full text of the Wikipedia article if successful, otherwise returns None.

    '''
    search_url = f"https://en.wikipedia.org/wiki/{company_name_or_ticker}"
    
    try:
        response = requests.get(search_url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        title = soup.find('h1', {'id': 'firstHeading'}).text

        content_div = soup.find('div', {'id': 'mw-content-text'})

        paragraphs = content_div.find_all('p')

        full_article_text = '\n\n'.join([p.text.strip() for p in paragraphs if p.text.strip()])

        #print(f"Title: {title}")
        #print(f"Full Article:\n{full_article_text}")
        return f"{title}" + " " + f"{full_article_text}"

    except requests.exceptions.RequestException as e:
        print(f"Error fetching the Wikipedia article: {e}")
        return None

def wikipedia_ner_rel_pipeline(ticker):
    '''
    Extracts entities and relationships from a company's Wikipedia article using NLP API requests.

    Parameters:

    ticker (str): Company ticker symbol.
    Returns:

    tuple: DataFrames of entities and relationships extracted from the Wikipedia article.

    '''
    company_name = get_company_name(ticker)
    article = get_wikipedia_article(company_name)
    if article is None:
        return (None,None)
    res = get_request({
    "content": article,
    "lang": "en",
    "format": "plain text with title",
    })
    ents, rels = None, None
    try:
        ents = extract_entites(res)
        rels = extract_relationships(res)
    except:
        print("Rate Limit Exceeded. Natural Language API allows only 500 calls a month.")
        print(res)
        return (None, None)
    
    pd.options.display.max_columns = None
    pd.set_option('display.width', 3000)
    
    return (ents, rels)

def sec_10k_ner_rel_pipeline(ticker):
    '''
    Extracts entities and relationships from SEC 10-K's Item 1 and Item 7 forms for the specified company using Diffbot's NLP API requests.

    Parameters:

    ticker (str): Company ticker symbol.
    Returns:

    tuple: DataFrames of entities and relationships extracted from 10-K sections.
    '''

    dbpath = 'data/ecmdatabase.db'
    con = sqlite3.connect(f"file:{dbpath}?mode=ro", uri=True)
    with con:
        result = con.execute(f"SELECT * from companies WHERE stock_symbol = '{ticker}';")
        records = result.fetchall()
        if records == []:
            print(f"no records of company ticker {ticker} found in database.")
            return (None,None)
        company_name = records[0][1]
        item1 = records[0][2].replace('\n', '')
        item7 = records[0][3].replace('\n', '')
    item1_res = get_request({
    "content": item1,
    "lang": "en",
    "format": "plain text",
    })
    item7_res = get_request({
    "content": item7,
    "lang": "en",
    "format": "plain text",
    })

    try:
        item1_ents, item1_rels = extract_entites(item1_res), extract_relationships(item1_res)
        item7_ents, item7_rels = extract_entites(item7_res), extract_relationships(item7_res)
    except:
        print("Rate Limit Exceeded. Natural Language API allows only 500 calls a month.")
        print(item1_res)
        return (None, None)

    ents = pd.concat([item1_ents,item7_ents],axis = 0)
    rels = pd.concat([item1_rels,item7_rels],axis = 0)

    return (ents,rels)

def create_json_schema():
    '''
    Description:
    Generates an empty JSON schema with predefined nodes and relationships.

    Returns:

    dict: JSON structure representing the knowledge graph schema.

    '''
    json_schema = {
        "nodes":{
            "Company":[
                # company nodes here
            ],
            "Country":[
                # country nodes here
            ],
            "Industry":[
                # industry nodes here
            ],
            "Region":[
                # region nodes here
            ],

            "Product":[
                # product nodes here
            ]
        },
        
        "relationships":{
            "PARTNERS_WITH":[
                # (COMPANY cid1)-[:PARTNERS_WITH]->(COMPANY cid2)
            ],
            "COMPETES_WITH":[
                # (COMPANY cid1)-[:COMPETES_WITH]->(COMPANY cid2)
            ],
            "SUBSIDIARY_OF":[
                # (COMPANY cid1)-[:SUBSIDIARY_OF]->(COMPANY cid2)
            ],

            "HEADQUARTERS_IN":[
                # (COMPANY cid)-[:HEADQUARTERS_IN]->(COUNTRY ctyid)
            ],

            "OPERATES_IN_COUNTRY":[
                # (COMPANY cid)-[:OPERATES_IN_COUNTRY]->(COUNTRY ctyid)
            ],

            "IS_INVOLVED_IN":[
                # (COMPANY cid)-[:IS_INVOLVED_IN]->(INDUSTRY iid)
            ],

            "IS_IN":[
                # (COUNTRY ctyid)-[:IS_IN]->(REGION rid)
            ],

            "OPERATES_IN_REGION":[
                # (COMPANY cid)-[:OPERATES_IN_REGION]->(REGION rid)
            ],

            "PRODUCES":[
                # (COMPANY cid)-[:PRODUCES]->(PRODUCT)
            ]
    }}


    return json_schema

def create_company_node(name,ticker_code = None,founded_year = None):
    '''
    Description:
    Creates a dictionary for a company node with optional ticker and founded year attributes.

    Parameters:

    name (str): Company name.
    ticker_code (str, optional): Ticker code of the company.
    founded_year (int, optional): Year the company was founded.

    Returns:

    dict: Dictionary containing the company node data.

    '''
    c_node = {}

    c_node["name"] = name

    if ticker_code is None:
        c_node["ticker_code"] = get_company_ticker(name)
    else:
        c_node["ticker_code"] = ticker_code
    c_node["founded_year"] = founded_year

    return c_node

def city_to_country(city):
    '''
    Description:
    Fetches the country name for a given city by querying the Geonames website.

    Parameters:

    city (str): City name.

    Returns:

    str: Country name if found, otherwise Not Found.

    '''
    response = requests.request("GET", f"https://www.geonames.org/search.html?q={city}&country=")
    country_raw = re.findall("/countries.*\\.html", response.text)
    if len(country_raw) != 0:
        country_pred = country_raw[0].strip(".html").split("/")[-1]
        country = country_pred.replace('-',' ').title()
        return country
    else:
        return "Not Found"

def country_to_continent(country_name):
    '''
    Description:
    Converts a country name to its respective continent name.

    Parameters:

    country_name (str): Name of the country.

    Returns:

    str: Continent name.

    '''
    country_alpha2 = pc.country_name_to_country_alpha2(country_name)
    country_continent_code = pc.country_alpha2_to_continent_code(country_alpha2)
    country_continent_name = pc.convert_continent_code_to_continent_name(country_continent_code)
    return country_continent_name

def create_country_node(name,iso3=None,iso2=None, population = None, gdp = None, corporate_tax_rate = None, is_city = False):
    '''
    Description:
    Creates a country node with optional fields and determines country details based on the is_city parameter.

    Parameters:

    name (str): Country name.
    iso3, iso2, population, gdp, corporate_tax_rate (optional): Additional country details.
    is_city (bool): Whether the name provided is a city (in which case, it will look up the country).

    Returns:

    dict: Country node details.

    '''
    cnty_node = {}
    
    if is_city:
        cnty_node["source_city"] = name
        name = city_to_country(name)
    cnty_node["name"] = name
    try:
        cnty_node["iso2"] = pc.country_name_to_country_alpha2(name)
        cnty_node["iso3"] = pc.country_name_to_country_alpha3(name)
    except KeyError:
        print(f"{cnty_node['name']} could not be mapped to iso code")
    
    cnty_node["population"] = random.randint(5,1400) #in millions
    cnty_node["gdp"] = random.randint(1,20000) #in billions
    cnty_node["corporate_tax_rate"] = random.randint(10,50)

    return cnty_node

def create_region_node(cnty_node):
    '''
    Description:
    Creates a region node based on the country node provided.

    Parameters:

    cnty_node (dict): Country node data.

    Returns:

    dict: Region node data.
    
    '''
    reg_node = {}
    if cnty_node.get("iso2",None) is None:
        reg_node["name"] = "Not Found"
    else:
        reg_node["name"] = pc.country_alpha2_to_continent_code(cnty_node["iso2"])

    reg_node["m49"] = None
    return reg_node

def create_industry_node(name,SIC_code = None, industry_group = None, subindustry_desc = None, primary_activity= None):

    '''
    Description:

    Creates an industry node with optional attributes.

    Parameters:

    name (str): Industry name.
    SIC_code, industry_group, subindustry_desc, primary_activity (optional): Additional industry details.

    Returns:

    dict: Industry node details.

    '''
    ind_node = {}
    ind_node["name"] = name
    ind_node["SIC_code"] = SIC_code
    ind_node["industry_group"] = industry_group
    ind_node["subindustry_desc"] = subindustry_desc
    ind_node["primary_activity"] = primary_activity
    return ind_node

def create_product_node(name):
    '''
    Description:
    Creates a product node.

    Parameters:

    name (str): Product name.

    Returns:

    dict: Product node details.

    '''
    pdt_node = {}
    pdt_node["name"] = name
    return pdt_node

def create_hq_rel(c_node, cnty_node):
    '''
    Description:
    Creates a "HEADQUARTERS_IN" relationship between a company and a country.

    Parameters:

    c_node (dict): Company node data.
    cnty_node (dict): Country node data.

    Returns:

    dict: A dictionary with details about the "HEADQUARTERS_IN" relationship, namely
    "c_node": The name of the company.
    "cnty_node": The name of the country.
    
    '''
    hq_rel = {}
    hq_rel["company_name"] = c_node["name"]
    hq_rel["country_name"] = cnty_node["name"]

    return hq_rel

def create_operates_in_country_rel(c_node, cnty_node):
    '''
    Description:
    Generates a relationship dictionary that links a company with a country where it operates.

    Parameters:

    c_node (dict): A dictionary representing a company node, containing at least the "name" key.
    cnty_node (dict): A dictionary representing a country node, containing at least the "name" key.

    Returns:

    oic_rel (dict): A dictionary with details about the "operates in country" relationship, including:
    "company_name": Name of the company.
    "country_name": Name of the country.
    "net sales": Randomly generated net sales figure (between -30 million and 30 million).
    "headcount": Randomly generated headcount (between 1 and 10,000).

    '''
    oic_rel = {}
    oic_rel["company_name"] = c_node["name"]
    oic_rel["country_name"] = cnty_node["name"]
    oic_rel["net sales"] = random.randint(-30000000,30000000)
    oic_rel["headcount"] = random.randint(1,10000)

    return oic_rel

def create_operates_in_region_rel(c_node, reg_node):
    '''
    Description:
    Generates a relationship dictionary that links a company with a country where it operates.

    Parameters:

    c_node (dict): A dictionary representing a company node, containing at least the "name" key.
    cnty_node (dict): A dictionary representing a country node, containing at least the "name" key.
    Returns:

    oic_rel (dict): A dictionary with details about the "operates in country" relationship, including:
    "company_name": Name of the company.
    "country_name": Name of the country.
    "net sales": Randomly generated net sales figure (between -30 million and 30 million).
    "headcount": Randomly generated headcount (between 1 and 10,000).

    '''
    oir_rel = {}
    oir_rel["company_name"] = c_node["name"]
    oir_rel["region_name"] = reg_node["name"]
    oir_rel["net sales"] = random.randint(-30000000,30000000)
    oir_rel["headcount"] = random.randint(100,1000000)

    return oir_rel

def create_is_in_rel(cnty_node, reg_node):
    '''
    Creates a relationship dictionary indicating that a country is part of a specific region.

    Parameters:

    cnty_node (dict): A dictionary representing a country node, containing at least the "name" key.
    reg_node (dict): A dictionary representing a region node, containing at least the "name" key.
    Returns:

    is_in_rel (dict): A dictionary with details about the "is in region" relationship, including:
    "country_name": Name of the country.
    "region_name": Name of the region.

    '''
    is_in_rel = {}
    is_in_rel["country_name"] = cnty_node["name"]
    is_in_rel["region_name"] = reg_node["name"]

    return is_in_rel


#partners, competitors, subsidiaries 
def create_company_company_rel(c_node1, c_node2, type = None):
    '''
    Description:
    Creates a relationship dictionary between two companies, which may represent partnerships, competition, or subsidiary relationships.

    Parameters:

    c_node1 (dict): A dictionary representing the first company node, containing at least the "name" key.
    c_node2 (dict): A dictionary representing the second company node, containing at least the "name" key.
    type (str, optional): Type of relationship (e.g., "partners", "competitors", or "subsidiary").

    Returns:

    c_c_rel (dict): A dictionary with details about the company-to-company relationship, including:
    "company_name_1": Name of the first company.
    "company_name_2": Name of the second company.
    "type": Type of relationship, if specified.

    '''
    c_c_rel = {}
    c_c_rel["company_name_1"] = c_node1["name"]
    c_c_rel["company_name_2"] = c_node2["name"]
    c_c_rel["type"] = type

    return c_c_rel

def create_in_industry_rel(c_node, ind_node):
    '''
    Description:
    Generates a relationship dictionary linking a company to an industry in which it is involved.

    Parameters:

    c_node (dict): A dictionary representing a company node, containing at least the "name" key.
    ind_node (dict): A dictionary representing an industry node, containing at least the "name" key.

    Returns:

    c_ind_rel (dict): A dictionary with details about the "is involved in industry" relationship, including:
    "company_name": Name of the company.
    "industry_name": Name of the industry.

    '''
    c_ind_rel = {}
    c_ind_rel["company_name"] = c_node["name"]
    c_ind_rel["industry_name"] = ind_node["name"]
    return c_ind_rel

def create_produces_rel(c_node, pdt_node):
    '''
    Description:
    Creates a relationship dictionary representing that a company produces a specific product.

    Parameters:

    c_node (dict): A dictionary representing a company node, containing at least the "name" key.
    pdt_node (dict): A dictionary representing a product node, containing at least the "name" key.

    Returns:

    c_pdt_rel (dict): A dictionary with details about the "produces" relationship, including:
    "company_name": Name of the company.
    "product_name": Name of the product.

    '''
    c_pdt_rel = {}
    c_pdt_rel["company_name"] = c_node["name"]
    c_pdt_rel["product_name"] = pdt_node["name"]

    return c_pdt_rel

def generate_json_schema(json, offset = 0):
    '''
    Description:
    Populates a JSON schema with entities and relationships from SEC 10-K and Wikipedia pipelines for companies in the database.

    Parameters:

    json (dict): The initial JSON schema to be populated.
    offset (int, optional): Starting offset for the database query.

    Returns:

    dict: JSON schema populated with nodes and relationships.
    
    '''
    dbpath = 'data/ecmdatabase.db'
    con = sqlite3.connect(f"file:{dbpath}?mode=ro", uri=True)

    def fill_entities(ents, c_name):
        if ents is None:
            return None
        
        for _,ent in ents.iterrows():
            if ent["Labels"] =='company' and ent["name"] != c_name:
                ticker = get_company_ticker(ent["name"])
                json["nodes"]["Company"].append(create_company_node(ent["name"],ticker))
            elif ent["Labels"] == 'industry':
                json["nodes"]["Industry"].append(create_industry_node(ent["name"]))
            elif ent["Labels"] == 'country':
                json["nodes"]["Country"].append(create_country_node(ent["name"]))
            elif ent["Labels"] == 'location':
                json["nodes"]["Country"].append(create_country_node(ent["name"], is_city = True))
            elif ent["Labels"] == 'product':
                json["nodes"]["Product"].append(create_product_node(ent["name"]))

        for cnty_node in json["nodes"]["Country"]:
            reg_node = create_region_node(cnty_node)
            json["nodes"]["Region"].append(reg_node)
            json["relationships"]["IS_IN"].append(create_is_in_rel(cnty_node,reg_node))
    
    def fill_relationships(rels):
        if rels is None:
            return None
        
        for _, rel in rels.iterrows():
            if rel["property"] == "headquarters":
                c_node = create_company_node(rel["entity"])
                hq_node = create_country_node(rel["value"], is_city = True)
                hq_rel = create_hq_rel(c_node, hq_node)
                json["relationships"]["HEADQUARTERS_IN"].append(hq_rel)
            elif rel["property"] == "organization locations":
                c_node = create_company_node(rel["entity"])
                loc_node = create_country_node(rel["value"],is_city = True)
                loc_rel = create_operates_in_country_rel(c_node, loc_node)
                json["relationships"]["OPERATES_IN_COUNTRY"].append(loc_rel)
            elif rel["property"] == "industry":
                c_node = create_company_node(rel["entity"])
                ind_node = create_industry_node(rel["value"])
                works_rel = create_in_industry_rel(c_node, ind_node)
                json["relationships"]["IS_INVOLVED_IN"].append(works_rel)
            elif rel["property"] == "product type":
                c_node = create_company_node(rel["entity"])
                pdt_node = create_product_node(rel["value"])
                produces_rel = create_produces_rel(c_node,pdt_node)
                json["relationships"]["PRODUCES"].append(produces_rel)

            elif rel["property"] == "competitors":
                c_node_1 = create_company_node(rel["entity"])
                c_node_2 = create_company_node(rel["value"])
                cc_rel = create_company_company_rel(c_node_1,c_node_2)
                json["relationships"]["COMPETES_WITH"].append(cc_rel)
            elif rel["property"] == "suppliers":
                c_node_1 = create_company_node(rel["entity"])
                c_node_2 = create_company_node(rel["value"])
                cc_rel = create_company_company_rel(c_node_1,c_node_2, "suppliers")
                json["relationships"]["PARTNERS_WITH"].append(cc_rel)
            elif rel["property"] == "subsidiary":
                c_node_1 = create_company_node(rel["entity"])
                c_node_2 = create_company_node(rel["value"])
                cc_rel = create_company_company_rel(c_node_2, c_node_1, "subsidiary")
                json["relationships"]["SUBSIDIARY_OF"].append(cc_rel)
    with con:
        result = con.execute(f"SELECT name, stock_symbol from companies ORDER BY name LIMIT 10 OFFSET {offset};")
        records = result.fetchall()
        for record in records:
            company_name = record[0]
            stock_code = record[1]

            print(f'Processing {company_name} with stock code {stock_code}')
            
            c_node = create_company_node(company_name,stock_code)
            json["nodes"]["Company"].append(c_node)

            sec_10k_ents, sec_10k_rels = sec_10k_ner_rel_pipeline(stock_code)
            wiki_ents, wiki_rels = wikipedia_ner_rel_pipeline(stock_code)

            fill_entities(sec_10k_ents,company_name)
            fill_entities(wiki_ents,company_name)
            fill_relationships(sec_10k_rels)
            fill_relationships(wiki_rels)
    
    return json

empty_schema = create_json_schema()
print("Generating schema")

if not os.path.exists('output'):
    os.makedirs('output')
output_file = os.getenv("ER_EXTRACTION_OUTPUT", "nasdaq_kg_schema.json")
try:
    kg_json = generate_json_schema(empty_schema, offset = 0)
    with open(f'output/{output_file}', 'w') as f:
        json.dump(empty_schema, f)
        print("Schema generated successfully!")
except Exception as error:
    print("Error encountered. Schema generated is incomplete.")
    print(error)
    with open('output/nasdaq_kg_schema.json', 'w') as f:
        json.dump(empty_schema, f)

