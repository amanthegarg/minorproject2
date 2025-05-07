import streamlit as st
import pandas as pd
import os
import hashlib
import time
from scholarly import scholarly
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from openai import OpenAI
import openai
from dotenv import load_dotenv


# Load environment variables from .env file
load_dotenv()

# Fetch API key
api_key = os.getenv("OPENAI_API_KEY")

# Check if API key was loaded
if not api_key:
    raise ValueError("❌ OPENAI_API_KEY not found. Make sure it's defined in the .env file.")

# Initialize OpenAI client
client = OpenAI(api_key=api_key)



# Or for testing (not recommended for production)
# openai.api_key = "your-openai-key"



# --- File Paths ---
authorlist_file = "authorlist.csv"
output_file = "Example Outputs/ss_output_data.csv"
user_db_file = "user_db.csv"

# --- Helper Functions ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def authenticate(username, password):
    if os.path.exists(user_db_file):
        users = pd.read_csv(user_db_file)
        hashed = hash_password(password)
        if username in users['username'].values:
            stored_hash = users.loc[users['username'] == username, 'password'].values[0]
            return hashed == stored_hash
    return False

def register_user(username, password):
    new_user = pd.DataFrame([[username, hash_password(password)]], columns=["username", "password"])
    if os.path.exists(user_db_file):
        users = pd.read_csv(user_db_file)
        if username in users['username'].values:
            return False
        users = pd.concat([users, new_user], ignore_index=True)
    else:
        users = new_user
    users.to_csv(user_db_file, index=False)
    return True

def get_chrome_options():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-logging')
    options.add_argument('--log-level=3')
    return options

def get_driver():
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=get_chrome_options())

def get_gsid_for_name(name, driver, wait):
    query = f"{name} jiit".replace(" ", "+")
    driver.get(f"https://scholar.google.com/citations?view_op=search_authors&mauthors={query}")
    try:
        link = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'h3.gs_ai_name a')))
        email = driver.find_element(By.CSS_SELECTOR, '.gs_ai_eml').text
        if 'jiit.ac.in' not in email.lower():
            return "Not Found"
        return link.get_attribute('href').split('user=')[1].split('&')[0]
    except:
        return "Not Found"

def extract_pdf_link(pub_url, driver):
    try:
        driver.get(pub_url)
        time.sleep(2)

        try:
            pdf_link_element = driver.find_element(By.XPATH, "//a[.//span[contains(text(), '[PDF]')]]")
            pdf_link = pdf_link_element.get_attribute('href')
            if pdf_link.lower().endswith(".pdf") or "pdf" in pdf_link.lower():
                return pdf_link
        except:
            pass

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if ".pdf" in href.lower():
                return href
        return ""
    except:
        return ""

def convert_dataframe_types(df):
    for column in df.columns:
        if df[column].dtype == 'object':
            df[column] = df[column].astype(str).fillna("")
        elif pd.api.types.is_numeric_dtype(df[column]):
            df[column] = pd.to_numeric(df[column], errors='coerce').fillna(0)
        elif pd.api.types.is_bool_dtype(df[column]):
            df[column] = df[column].fillna(False)
    return df

def get_author_data_context(author_name, df):
    row = df[df["Name"] == author_name]
    if row.empty:
        return ""
    context = row.to_dict(orient="records")[0]
    formatted = "\n".join([f"{k}: {v}" for k, v in context.items()])
    return f"Author Data:\n{formatted}"


# --- Streamlit App ---
st.set_page_config(page_title="JIIT Faculty GS Viewer", layout="wide")

# Authentication check
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔐 Login / Signup")
    auth_mode = st.radio("Choose Option", ["Login", "Signup"])
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Submit") and username and password:
        if auth_mode == "Login":
            if authenticate(username, password):
                st.success("✅ Logged in successfully!")
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("❌ Invalid credentials.")
        else:
            if register_user(username, password):
                st.success("✅ Registered successfully! Please login.")
            else:
                st.error("⚠️ Username already exists.")

if st.session_state.authenticated:
    # Load or initialize output_df
    if os.path.exists(output_file):
        output_df = pd.read_csv(output_file, sep='|')
    else:
        output_df = pd.DataFrame(columns=[
            "Name", "Name on Profile", "Scholar ID", "Cited by", "h-index",
            "i10-index", "Affiliation", "Document Count", "Publications", "PDF Links"
        ])

    st.title("📚 EDU SCRAPE")

    authorlist_df = pd.read_csv(authorlist_file) if os.path.exists(authorlist_file) else pd.DataFrame(columns=["Name"])
    authors = authorlist_df["Name"].tolist()

    st.markdown("---")
    st.subheader("👤 Author Selection")

    col1, col2 = st.columns(2)
    with col1:
        mode = st.radio("Mode", ["Select Existing", "Add New"])
    with col2:
        author_name = st.selectbox("Choose Author", options=authors) if mode == "Select Existing" else st.text_input("Enter New Author Name")

    st.markdown("")

    col3, col4 = st.columns([1, 1])
    with col3:
        fetch_clicked = st.button("📥 Fetch / View Data")
    with col4:
        update_clicked = st.button("🔄 Update PDF Links")

    # Chatbot Section in Sidebar
    st.sidebar.markdown("---")
    st.sidebar.subheader("❓ Ask a Question About the Author")

    user_question = ""
    if author_name:
        user_question = st.sidebar.text_input("Ask a question (e.g., What is the h-index?)")

    if st.sidebar.button("🔎 Get Answer") and user_question and author_name:
        context = get_author_data_context(author_name, output_df)
        prompt = f"{context}\n\nQuestion: {user_question}\nAnswer:"
        with st.spinner("Thinking..."):
            try:
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant that answers questions based on faculty publication data."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=300
                )
                answer = response.choices[0].message.content
                st.sidebar.success("🧠 Answer:")
                st.sidebar.markdown(answer)
            except Exception as e:
                st.sidebar.error(f"❌ Failed to get response: {e}")

    # Fetch or update author data
    if fetch_clicked and author_name:
        existing_row = output_df[output_df["Name"] == author_name]

        if not existing_row.empty:
            st.success("✅ Data found. Showing stored info.")
            row = existing_row.iloc[0]
            with st.expander("📄 Basic Info", expanded=True):
                clean_row = row.drop(labels=[col for col in ["Publications", "PDF Links"] if col in row])
                df_info = pd.DataFrame(clean_row.items(), columns=["Field", "Value"])

                # Display data in table format
                st.dataframe(
                    df_info,
                    use_container_width=True,
                    hide_index=True
                )

            with st.expander("📚 Publications"):
                pubs = [p.strip() for p in str(row.get("Publications", "")).split(";") if p.strip()]
                pdfs = [p.strip() for p in str(row.get("PDF Links", "")).split(";") if p.strip()]
                for i, pub in enumerate(pubs):
                    if i < len(pdfs) and pdfs[i].startswith("http"):
                        st.markdown(f"- [{pub}]({pdfs[i]}) 🔗")
                    else:
                        st.markdown(f"- {pub}")
        else:
            st.info("🔍 Scraping Google Scholar...")
            driver = get_driver()
            wait = WebDriverWait(driver, 10)
            gsid = get_gsid_for_name(author_name, driver, wait)

            if gsid == "Not Found":
                driver.quit()
                st.error("❌ GSID not found or not a JIIT-affiliated author.")
                st.stop()

            try:
                author_info = scholarly.search_author_id(gsid)
                data_dict = scholarly.fill(author_info, sections=['basics', 'indices', 'publications', 'counts', 'coauthors'])
                pubs, pdf_links = [], []

                for pub in data_dict['publications']:
                    title = pub['bib']['title'].replace("|", " ")
                    pub_url = pub.get("pub_url", "")
                    pdf = extract_pdf_link(pub_url, driver) if pub_url else ""
                    pubs.append(title)
                    pdf_links.append(pdf)

                coauthors = [co['name'] for co in data_dict.get("coauthors", [])]

                profile_info = {
                    "Name on Profile": data_dict.get("name", ""),
                    "Scholar ID": data_dict.get("scholar_id", ""),
                    "Cited by": data_dict.get("citedby", ""),
                    "h-index": data_dict.get("hindex", ""),
                    "i10-index": data_dict.get("i10index", ""),
                    "Affiliation": data_dict.get("affiliation", ""),
                    "Document Count": len(pubs),
                    "Coauthors": ", ".join(coauthors),
                }

                st.success("✅ Data retrieved.")

                with st.expander("📄 Basic Info", expanded=True):
                    df_info = pd.DataFrame(profile_info.items(), columns=["Field", "Value"])

                    # Display data in table format
                    st.dataframe(
                        df_info,
                        use_container_width=True,
                        hide_index=True
                    )

                with st.expander("📚 Publications"):
                    for i, pub in enumerate(pubs):
                        if i < len(pdf_links) and pdf_links[i].startswith("http"):
                            st.markdown(f"- [{pub}]({pdf_links[i]}) 🔗")
                        else:
                            st.markdown(f"- {pub}")

                if author_name not in authors:
                    authorlist_df = pd.concat([authorlist_df, pd.DataFrame([{"Name": author_name}])], ignore_index=True)
                    authorlist_df.to_csv(authorlist_file, index=False)

                row = {
                    "Name": author_name,
                    "Name on Profile": profile_info["Name on Profile"],
                    "Scholar ID": profile_info["Scholar ID"],
                    "Cited by": profile_info["Cited by"],
                    "h-index": profile_info["h-index"],
                    "i10-index": profile_info["i10-index"],
                    "Affiliation": profile_info["Affiliation"],
                    "Document Count": profile_info["Document Count"],
                    "Coauthors": profile_info["Coauthors"],
                    "Publications": "; ".join(pubs),
                    "PDF Links": "; ".join(pdf_links),
                }

                output_df = output_df[output_df["Name"] != author_name]
                output_df = pd.concat([output_df, pd.DataFrame([row])], ignore_index=True)
                output_df = convert_dataframe_types(output_df)
                output_df.to_csv(output_file, sep='|', index=False)
                st.success("📁 Data saved.")
            except Exception as e:
                driver.quit()
                st.error(f"❌ Error fetching author info: {str(e)}")

    # Update PDF Links
   
    if update_clicked and author_name:
        st.info("Updating PDF links...")
    driver = get_driver()
    wait = WebDriverWait(driver, 10)
    try:
        gsid = get_gsid_for_name(author_name, driver, wait)
        if gsid == "Not Found":
            driver.quit()
            st.error("Scholar ID not found or author not affiliated.")
            st.stop()

        profile_url = f"https://scholar.google.com/citations?user={gsid}&hl=en"
        driver.get(profile_url)
        time.sleep(2)

        pubs, pdf_links = [], []

        # Find all publication links
        pub_elements = driver.find_elements(By.CSS_SELECTOR, 'a.gsc_a_at')
        pub_titles = [el.text for el in pub_elements]
        pub_hrefs = [el.get_attribute("href") for el in pub_elements]

        for i, pub_href in enumerate(pub_hrefs):
            driver.get(pub_href)
            time.sleep(2)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            pdf_url = ""
            for a in soup.find_all("a", href=True):
                if ".pdf" in a["href"].lower() and "[PDF]" in a.text:
                    pdf_url = a["href"]
                    break

            pubs.append(pub_titles[i])
            pdf_links.append(pdf_url)

        driver.quit()

        output_df.loc[output_df["Name"] == author_name, "Publications"] = "; ".join(pubs)
        output_df.loc[output_df["Name"] == author_name, "PDF Links"] = "; ".join(pdf_links)
        output_df = convert_dataframe_types(output_df)
        output_df.to_csv(output_file, sep='|', index=False)
        st.success("PDF Links updated.")
    except Exception as e:
        driver.quit()
        st.error(f"Error updating PDFs: {str(e)}")


    # Option to download the full dataset
    if os.path.exists(output_file):
        with open(output_file, "rb") as f:
            st.download_button("📂 Download All Data", f, "faculty_data.csv", mime="text/csv")
