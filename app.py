import json
import os
import queue
import time
from datetime import datetime
import pandas as pd
import speech_recognition as sr
import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# Load GEMINI_API_KEY from .env
load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

EXCEL_FILE = "Clinical_Ledger.xlsx"

# =============================
# 📋 AI STRUCTURED OUTPUT SCHEMA
# =============================
class ClinicalAnalysis(BaseModel):
    Symptom_Observation: str = Field(description="Key symptoms and duration reported by the patient.")
    Diagnosis_Condition: str = Field(description="Primary suspected diagnosis or condition.")
    Medication_Prescribed: str = Field(description="Medications prescribed with dosage/frequency, or 'None mentioned'.")
    Actionable_Orders: str = Field(description="Lab tests, imaging, or follow-up instructions.")
    Clinical_Summary: str = Field(description="Concise 2-sentence clinical summary of the consultation.")
    Risk_Level: str = Field(description="Must be strictly one of: Low, Medium, or High")

# =============================
# 📁 EXCEL LEDGER SETUP
# =============================
def initialize_ledger():
    if not os.path.exists(EXCEL_FILE):
        df = pd.DataFrame(columns=[
            "Date", "Patient Name", "Symptoms", "Diagnosis",
            "Medications", "Orders", "Summary", "Risk", "Transcript"
        ])
        df.to_excel(EXCEL_FILE, index=False)

# =============================
# 🤖 REAL GEMINI AI ANALYSIS (WITH RETRY & FALLBACK)
# =============================
def generate_notes(conversation_text: str) -> dict:
    prompt = f"""
    You are MediScribe+, an AI Clinical Documentation Assistant.
    Analyze the following live doctor-patient consultation transcript and extract accurate clinical insights.
    Do not invent facts not present in the transcript.

    Consultation Transcript:
    {conversation_text}
    """
    models_to_try = ["gemini-3.8-flash", "gemini-3.8-flash-lite"]
    last_error = None

    for model_name in models_to_try:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ClinicalAnalysis,
                        temperature=0.2,
                    ),
                )
                return response.parsed.model_dump()
            except Exception as e:
                last_error = e
                if "503" in str(e) or "UNAVAILABLE" in str(e):
                    time.sleep(1.5)
                    continue
                raise e

    raise last_error

# =============================
# 💾 SAVE TO EXCEL LEDGER
# =============================
def save_to_excel(patient_name: str, conversation: str, notes: dict):
    row_data = {
        "Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Patient Name": patient_name,
        "Symptoms": notes.get("Symptom_Observation"),
        "Diagnosis": notes.get("Diagnosis_Condition"),
        "Medications": notes.get("Medication_Prescribed"),
        "Orders": notes.get("Actionable_Orders"),
        "Summary": notes.get("Clinical_Summary"),
        "Risk": notes.get("Risk_Level"),
        "Transcript": conversation.strip()
    }
    df_new = pd.DataFrame([row_data])
    try:
        existing_df = pd.read_excel(EXCEL_FILE)
        df_final = pd.concat([existing_df, df_new], ignore_index=True)
    except Exception:
        df_final = df_new

    df_final.to_excel(EXCEL_FILE, index=False)

# =============================
# 🔍 SEARCH PATIENT RECORDS
# =============================
def search_records(name: str):
    try:
        df = pd.read_excel(EXCEL_FILE)
        if not name.strip():
            return df
        return df[df["Patient Name"].astype(str).str.contains(name, case=False, na=False)]
    except Exception:
        return None

# =============================
# 🚀 STREAMLIT UI
# =============================
st.set_page_config(page_title="MediScribe+ | AI Clinical Documentation", page_icon="🏥", layout="wide")

st.markdown("""
<style>
    .clinical-card {
        background-color: #1e2530;
        padding: 18px;
        border-radius: 12px;
        border-left: 5px solid #00b4d8;
        margin-bottom: 12px;
    }
</style>
""", unsafe_allow_html=True)

initialize_ledger()

# Initialize session states
if "conversation" not in st.session_state:
    st.session_state.conversation = ""
if "latest_notes" not in st.session_state:
    st.session_state.latest_notes = None
if "is_listening" not in st.session_state:
    st.session_state.is_listening = False
if "stop_listener" not in st.session_state:
    st.session_state.stop_listener = None
if "audio_queue" not in st.session_state:
    st.session_state.audio_queue = queue.Queue()

# Drain any captured speech chunks from the background queue into the transcript
while not st.session_state.audio_queue.empty():
    chunk = st.session_state.audio_queue.get_nowait()
    st.session_state.conversation = (st.session_state.conversation + " " + chunk).strip()

# Header
st.title("🏥 MediScribe+")
st.caption("Real-Time Voice-to-Clinical Ledger AI Documentation System")
st.divider()

left_col, right_col = st.columns([1.05, 1.15], gap="large")

with left_col:
    st.subheader("🎤 Live Consultation")
    patient_name = st.text_input("Patient Name", placeholder="e.g., Triveni")

    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([1.15, 1.15, 1, 0.85])

    # ▶️ START SPEAKING BUTTON
    with btn_col1:
        if st.button("▶️ Start Speaking", disabled=st.session_state.is_listening, use_container_width=True):
            try:
                recognizer = sr.Recognizer()
                recognizer.pause_threshold = 1.2
                mic = sr.Microphone()
                with mic as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.4)

                q = st.session_state.audio_queue

                def audio_callback(rec, audio):
                    try:
                        text = rec.recognize_google(audio)
                        q.put(text)
                    except Exception:
                        pass

                stop_fn = recognizer.listen_in_background(mic, audio_callback, phrase_time_limit=25)
                st.session_state.stop_listener = stop_fn
                st.session_state.is_listening = True
                st.rerun()
            except Exception as e:
                st.error(f"Mic Error: {e}")

    # ⏹️ STOP SPEAKING BUTTON
    with btn_col2:
        if st.button("⏹️ Stop Speaking", disabled=not st.session_state.is_listening, type="primary", use_container_width=True):
            if st.session_state.stop_listener is not None:
                st.session_state.stop_listener(wait_for_stop=False)
                st.session_state.stop_listener = None
            st.session_state.is_listening = False
            time.sleep(0.5)  # Allow final spoken phrase to finish transcribing
            while not st.session_state.audio_queue.empty():
                chunk = st.session_state.audio_queue.get_nowait()
                st.session_state.conversation = (st.session_state.conversation + " " + chunk).strip()
            st.rerun()

    # 📋 LOAD SAMPLE
    with btn_col3:
        if st.button("📋 Sample", use_container_width=True):
            st.session_state.conversation = (
                "Patient reports severe burning stomach pain and acidity after meals for 3 days, "
                "accompanied by mild nausea. No fever or vomiting. Diagnosis is acute gastritis. "
                "Prescribed Pantoprazole 40mg before breakfast and liquid antacid after meals. "
                "Order an abdominal ultrasound if pain persists beyond 5 days."
            )
            st.rerun()

    # 🧹 CLEAR
    with btn_col4:
        if st.button("🧹 Clear", use_container_width=True):
            if st.session_state.stop_listener is not None:
                st.session_state.stop_listener(wait_for_stop=False)
                st.session_state.stop_listener = None
            st.session_state.is_listening = False
            st.session_state.conversation = ""
            st.session_state.latest_notes = None
            st.rerun()

    # Live recording status indicator
    if st.session_state.is_listening:
        st.error("🔴 **Listening continuously...** Speak naturally (even with pauses). Click **⏹️ Stop Speaking** when done.")

    conversation_input = st.text_area(
        "📝 Live Conversation Transcript (Speak or Edit Directly)",
        value=st.session_state.conversation,
        height=200,
        placeholder="Click '▶️ Start Speaking' to record continuously until you click '⏹️ Stop Speaking'..."
    )
    st.session_state.conversation = conversation_input

    if st.button("🤖 Generate Clinical Report", type="primary", use_container_width=True):
        # Auto-stop mic if user clicks Generate directly
        if st.session_state.is_listening and st.session_state.stop_listener is not None:
            st.session_state.stop_listener(wait_for_stop=False)
            st.session_state.stop_listener = None
            st.session_state.is_listening = False

        if not patient_name.strip() or not st.session_state.conversation.strip():
            st.warning("⚠️ Please enter the Patient Name and record/type the consultation first.")
        else:
            with st.spinner("Analyzing consultation with Gemini AI..."):
                try:
                    notes = generate_notes(st.session_state.conversation)
                    st.session_state.latest_notes = notes
                    save_to_excel(patient_name.strip(), st.session_state.conversation, notes)
                    st.success("💾 Analysis complete & saved to Clinical_Ledger.xlsx!")
                except Exception as e:
                    st.error(f"AI Error: {e}")

with right_col:
    st.subheader("📋 Clinical Insights")
    if st.session_state.latest_notes:
        notes = st.session_state.latest_notes
        risk = notes["Risk_Level"]

        if risk.lower() == "high":
            st.error(f"🚨 **Risk Level: {risk}** — Immediate clinical attention advised")
        elif risk.lower() == "medium":
            st.warning(f"⚠️ **Risk Level: {risk}** — Monitor closely and follow up")
        else:
            st.success(f"✅ **Risk Level: {risk}** — Routine outpatient care")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"""
            <div class="clinical-card">
                <b>🩺 Diagnosis / Condition</b><br>{notes['Diagnosis_Condition']}
            </div>
            <div class="clinical-card">
                <b>🤒 Symptom Observation</b><br>{notes['Symptom_Observation']}
            </div>
            """, unsafe_allow_html=True)

        with c2:
            st.markdown(f"""
            <div class="clinical-card">
                <b>💊 Medications Prescribed</b><br>{notes['Medication_Prescribed']}
            </div>
            <div class="clinical-card">
                <b>🧪 Actionable Orders</b><br>{notes['Actionable_Orders']}
            </div>
            """, unsafe_allow_html=True)

        st.info(f"**📝 Clinical Summary:** {notes['Clinical_Summary']}")
    else:
        st.info("Click **▶️ Start Speaking** (or **📋 Sample**) on the left and click **🤖 Generate Clinical Report** to view structured clinical insights here.")

st.divider()

# =============================
# 🔍 RETRIEVE PATIENT RECORDS
# =============================
st.subheader("🔍 Retrieve Patient Records (Clinical Ledger)")

s_col1, s_col2, s_col3 = st.columns([2, 0.8, 1])
with s_col1:
    search_name = st.text_input("Search by Patient Name (leave blank to show all)", label_visibility="collapsed", placeholder="Search patient name or leave empty to view all records...")
with s_col2:
    search_clicked = st.button("🔍 Search Records", use_container_width=True)
with s_col3:
    if os.path.exists(EXCEL_FILE):
        with open(EXCEL_FILE, "rb") as f:
            st.download_button(
                "📥 Download Excel Ledger",
                data=f,
                file_name="Clinical_Ledger.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

records_df = search_records(search_name if search_clicked else "")
if records_df is not None and not records_df.empty:
    st.dataframe(records_df, use_container_width=True, hide_index=True)
else:
    st.caption("No patient records found in Clinical_Ledger.xlsx yet.")