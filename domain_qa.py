import streamlit as st
from PyPDF2 import PdfReader
import os
import io
from openai import OpenAI
import requests
import openai
import json
import time
import boto3
from botocore.exceptions import NoCredentialsError, ClientError
from dotenv import load_dotenv
from datetime import datetime
import pandas as pd

# Always load .env from the current directory
load_dotenv('.env', override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    st.error("❌ OpenAI API key not found. Please check your .env file in the project directory and ensure it contains OPENAI_API_KEY=sk-...your-key...")
    st.stop()

from openai import OpenAI
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Fetch environment variables
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_BUCKET_FEEDBACK = os.getenv("S3_BUCKET_FEEDBACK")
GITHUB_API_TOKEN = os.getenv("GITHUB_API_TOKEN")

# Debug: Print to confirm loading (never print real API keys in production!)
print("DEBUG - Env variables loaded:")
print(f"OPENAI_API_KEY: {'Loaded' if OPENAI_API_KEY else 'Missing'}")
print(f"AWS_ACCESS_KEY_ID: {'Loaded' if AWS_ACCESS_KEY_ID else 'Missing'}")
print(f"AWS_SECRET_ACCESS_KEY: {'Loaded' if AWS_SECRET_ACCESS_KEY else 'Missing'}")
print(f"AWS_REGION: {AWS_REGION}")
print(f"S3_BUCKET_NAME: {S3_BUCKET_NAME}")
# Set keys for libraries
import openai
openai.api_key = os.getenv("OPENAI_API_KEY")

# Interview status configurations
INTERVIEW_STATUSES = [
    {"name": "Screening", "color": "#ffeaa7", "icon": "📋"},
    {"name": "Ready for Evaluation", "color": "#74b9ff", "icon": "⏳"},
    {"name": "L1 Cleared", "color": "#00b894", "icon": "✅"},
    {"name": "L2 Cleared", "color": "#00cec9", "icon": "🎯"},
    {"name": "L3 Cleared", "color": "#6c5ce7", "icon": "🏆"},
    {"name": "Offered", "color": "#a29bfe", "icon": "💼"},
    {"name": "Rejected", "color": "#fd79a8", "icon": "❌"},
    {"name": "On Hold", "color": "#fdcb6e", "icon": "⏸️"}
]

def generate_questions_and_coding(interview_round, experience, skills):
    questions = []
    coding = []

    if not skills:
        skills = ["problem solving"]

    # Limit to 3 Q&A and 3 coding problems for performance
    limited_skills = skills[:3] if len(skills) > 0 else ["problem solving"]
    
    client = OpenAI()
    
    for i, skill in enumerate(limited_skills, 1):
        # Generate a general assessment question
        q = f"Generate an interview question (with answer) for a candidate with {experience} years experience in {skill}."
        
        # First, get the question
        q_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a technical interviewer."},
                {"role": "user", "content": q}
            ],
            max_tokens=100
        )
        question_text = q_response.choices[0].message.content.strip()

        # Now, get the model answer/solution
        answer_prompt = f"Given the following interview question, provide a model answer or solution that a strong candidate would give.\nQuestion: {question_text}"
        a_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a technical interviewer."},
                {"role": "user", "content": answer_prompt}
            ],
            max_tokens=250
        )
        model_answer = a_response.choices[0].message.content.strip()
        questions.append((question_text, model_answer))

        # Generate a detailed coding problem with complete solution
        coding_prompt = f"""
        Generate a coding problem for a candidate with {experience} years experience in {skill}.
        
        Provide the response in this EXACT format:
        
        **Problem Statement:** [Clear description of the coding problem]
        
        **Input:** [Sample input format and examples]
        
        **Output:** [Expected output format and examples]
        
        **Python Solution:**
        ```python
        [Complete working Python code solution]
        ```
        
        **Explanation:** [Brief explanation of the approach and algorithm]
        
        **Time Complexity:** [Big O notation]
        
        Make sure the problem is appropriate for {experience} years of experience and related to {skill}.
        """
        
        c_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a technical interviewer creating coding problems with complete solutions."},
                {"role": "user", "content": coding_prompt}
            ],
            max_tokens=500
        )
        coding_solution = c_response.choices[0].message.content.strip()
        coding.append((f"Coding Problem {i}", coding_solution))
    
    # Only return the first 3 Q&A and 3 coding problems
    return questions[:3], coding[:3]

def initialize_session_state():
    """Initialize session state variables for status tracking"""
    if 'candidate_statuses' not in st.session_state:
        st.session_state.candidate_statuses = {}
    if 'status_history' not in st.session_state:
        st.session_state.status_history = {}
    if 'interview_assessments' not in st.session_state:
        st.session_state.interview_assessments = []

def update_candidate_status(candidate_id, new_status, notes=""):
    """Update candidate status and maintain history"""
    old_status = st.session_state.candidate_statuses.get(candidate_id, "Screening")
    
    # Update current status
    st.session_state.candidate_statuses[candidate_id] = new_status
    
    # Update history
    if candidate_id not in st.session_state.status_history:
        st.session_state.status_history[candidate_id] = []
    
    st.session_state.status_history[candidate_id].append({
        "from_status": old_status,
        "to_status": new_status,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "notes": notes
    })

def get_candidates_by_status(status):
    """Get all candidates with a specific status"""
    candidates = []
    for candidate in candidate_profiles:
        candidate_status = st.session_state.candidate_statuses.get(candidate['id'], "Screening")
        if candidate_status == status:
            candidates.append(candidate)
    return candidates

def check_candidate_status_in_s3_csv(candidate_name):
    """Check if candidate exists in S3 CSV feedback file and return their status"""
    try:
        bucket_name = os.getenv('S3_BUCKET_FEEDBACK')
        if not bucket_name:
            return "Need to go with L1", "S3_BUCKET_FEEDBACK environment variable not set"
        
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-east-1')
        )
        
        feedback_key = "feedback/interview_feedback.xlsx"
        
        # Try to download the CSV file from S3
        try:
            response = s3_client.get_object(Bucket=bucket_name, Key=feedback_key)
            df = pd.read_excel(io.BytesIO(response['Body'].read()))
            
            # Check if candidate name exists in the CSV
            candidate_rows = df[df['candidate_name'].str.strip().str.lower() == candidate_name.strip().lower()]
            
            if not candidate_rows.empty:
                # Get the most recent entry (last row) for this candidate
                latest_entry = candidate_rows.iloc[-1]
                candidate_status = latest_entry.get('candidate_status', 'Unknown')
                return candidate_status, f"Found in feedback records"
            else:
                return "Need to go with L1", "Candidate not found in feedback records"
                
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                return "Need to go with L1", "Feedback file does not exist yet"
            else:
                return "Need to go with L1", f"Error accessing feedback file: {str(e)}"
                
    except Exception as e:
        return "Need to go with L1", f"Error checking candidate status: {str(e)}"

def render_round_dashboard(round_name, status_filter, next_round=None):
    """Render a dashboard for a specific interview round"""
    st.subheader(f"🎯 {round_name} Candidates")
    
    # Filter candidates for this round
    candidates = [c for c in candidate_profiles 
                 if st.session_state.candidate_statuses.get(c['id'], 'Screening') == status_filter]
    
    if not candidates:
        st.info(f"No candidates in {round_name} stage.")
        return
    
    # Display candidates in cards
    cols = st.columns(2)
    for idx, candidate in enumerate(candidates):
        with cols[idx % 2]:
            with st.container():
                st.markdown(f"""
                <div style="
                    background: white;
                    padding: 15px;
                    border-radius: 10px;
                    margin-bottom: 15px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                ">
                    <h4>👤 {candidate['candidate_name']}</h4>
                    <p><strong>Domain:</strong> {candidate['domain']}</p>
                    <p><strong>Experience:</strong> {candidate['experience_years']} years</p>
                    <p><strong>Skills:</strong> {', '.join(candidate['skills'][:3])}</p>
                </div>
                """, unsafe_allow_html=True)
                
                # Action buttons
                col1, col2 = st.columns(2)
                with col1:
                    if st.button(f"✅ Pass {round_name}", key=f"pass_{candidate['id']}_{round_name}"):
                        if next_round:
                            update_candidate_status(candidate['id'], next_round, f"Passed {round_name}")
                            st.rerun()
                with col2:
                    if st.button(f"❌ Fail {round_name}", key=f"fail_{candidate['id']}_{round_name}"):
                        update_candidate_status(candidate['id'], "Rejected", f"Did not pass {round_name}")
                        st.rerun()


def render_l1_dashboard():
    """Render L1 interview round dashboard"""
    render_round_dashboard("L1 Technical Round", "Screening", "Ready for Evaluation")


def render_l2_dashboard():
    """Render L2 interview round dashboard"""
    render_round_dashboard("L2 Technical Round", "Ready for Evaluation", "L1 Cleared")


def render_l3_dashboard():
    """Render L3 interview round dashboard"""
    render_round_dashboard("L3 Technical Round", "L1 Cleared", "L2 Cleared")


def render_status_tracking_dashboard():
    """Render the interview status tracking dashboard"""
    st.header("📊 Interview Status Dashboard")
    
    if not candidate_profiles:
        st.info("📝 No candidates available. Upload resumes first to track interview progress.")
        return
    
    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    
    total_candidates = len(candidate_profiles)
    offered_count = len([c for c in candidate_profiles if st.session_state.candidate_statuses.get(c['id'], 'Screening') == 'Offered'])
    rejected_count = len([c for c in candidate_profiles if st.session_state.candidate_statuses.get(c['id'], 'Screening') == 'Rejected'])
    in_progress = total_candidates - offered_count - rejected_count
    
    with col1:
        st.metric("Total Candidates", total_candidates)
    with col2:
        st.metric("In Progress", in_progress)
    with col3:
        st.metric("Offered", offered_count, delta=None)
    with col4:
        st.metric("Rejected", rejected_count)
    
    st.divider()
    
    # Interview Round Filter
    st.subheader("🎯 Interview Round Filter")
    
    col1, col2 = st.columns([1, 3])
    with col1:
        round_filter = st.selectbox(
            "Select Interview Round:",
            ["All Rounds", "L1 Round", "L2 Round", "L3 Round"],
            help="Filter candidates by specific interview rounds"
        )
    
    # Display filtered content based on selection
    if round_filter == "L1 Round":
        render_l1_dashboard()
    elif round_filter == "L2 Round":
        render_l2_dashboard()
    elif round_filter == "L3 Round":
        render_l3_dashboard()
    else:
        # Show all rounds - original Kanban board
        st.subheader("🎯 Complete Interview Pipeline")
    
    # Create columns for each status
    status_cols = st.columns(len(INTERVIEW_STATUSES))
    
    for idx, status_info in enumerate(INTERVIEW_STATUSES):
        with status_cols[idx]:
            status_name = status_info["name"]
            candidates = get_candidates_by_status(status_name)
            
            # Status column header
            st.markdown(f"""
            <div style="
                background-color: {status_info['color']};
                padding: 10px;
                border-radius: 8px;
                text-align: center;
                margin-bottom: 10px;
                font-weight: bold;
                color: #2d3436;
            ">
                {status_info['icon']} {status_name} ({len(candidates)})
            </div>
            """, unsafe_allow_html=True)
            
            # Display candidates in this status
            for candidate in candidates:
                with st.container():
                    st.markdown(f"""
                    <div style="
                        background-color: white;
                        border: 1px solid #ddd;
                        border-radius: 6px;
                        padding: 8px;
                        margin: 5px 0;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                    ">
                        <strong>{candidate['candidate_name']}</strong><br>
                        <small>{candidate['domain']} • {candidate['experience_years']}y</small><br>
                        <small>📅 {candidate['created_at']}</small>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # Quick action buttons for status change
                    if st.button(f"Move", key=f"move_{candidate['id']}_{status_name}", help=f"Change status for {candidate['candidate_name']}"):
                        st.session_state[f"show_status_dialog_{candidate['id']}"] = True
    
    st.divider()
    
    # Status change dialogs
    for candidate in candidate_profiles:
        if st.session_state.get(f"show_status_dialog_{candidate['id']}", False):
            with st.expander(f"🔄 Change Status: {candidate['candidate_name']}", expanded=True):
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    current_status = st.session_state.candidate_statuses.get(candidate['id'], "Screening")
                    st.info(f"Current Status: **{current_status}**")
                    
                    new_status = st.selectbox(
                        "Select New Status:",
                        [s["name"] for s in INTERVIEW_STATUSES],
                        index=[s["name"] for s in INTERVIEW_STATUSES].index(current_status),
                        key=f"status_select_{candidate['id']}"
                    )
                    
                    notes = st.text_area(
                        "Notes (optional):",
                        placeholder="Add any notes about this status change...",
                        key=f"status_notes_{candidate['id']}"
                    )
                
                with col2:
                    st.markdown("### 📋 Candidate Info")
                    st.markdown(f"**Domain:** {candidate['domain']}")
                    st.markdown(f"**Experience:** {candidate['experience_years']} years")
                    st.markdown(f"**Skills:** {', '.join(candidate['skills'][:3])}")
                
                # Action buttons
                col3, col4, col5 = st.columns(3)
                with col3:
                    if st.button("✅ Update Status", key=f"update_{candidate['id']}", type="primary"):
                        update_candidate_status(candidate['id'], new_status, notes)
                        st.session_state[f"show_status_dialog_{candidate['id']}"] = False
                        st.success(f"Status updated to: {new_status}")
                        st.rerun()
                
                with col4:
                    if st.button("❌ Cancel", key=f"cancel_{candidate['id']}"):
                        st.session_state[f"show_status_dialog_{candidate['id']}"] = False
                        st.rerun()
                
                with col5:
                    if st.button("📜 View History", key=f"history_{candidate['id']}"):
                        st.session_state[f"show_history_{candidate['id']}"] = True
    
    # Status history section
    st.subheader("📈 Status History & Audit Trail")
    
    # Filter options
    col1, col2 = st.columns(2)
    with col1:
        selected_candidate = st.selectbox(
            "Select Candidate:",
            ["All Candidates"] + [c['candidate_name'] for c in candidate_profiles],
            key="history_candidate_filter"
        )
    
    with col2:
        date_filter = st.date_input("From Date:", value=datetime.now().date())
    
    # Display history
    if selected_candidate == "All Candidates":
        # Show all candidates' history
        for candidate in candidate_profiles:
            candidate_id = candidate['id']
            if candidate_id in st.session_state.status_history:
                with st.expander(f"📋 {candidate['candidate_name']} - Status History"):
                    history = st.session_state.status_history[candidate_id]
                    
                    if history:
                        for entry in reversed(history):  # Most recent first
                            status_change_color = "#e8f5e8" if entry['to_status'] in ['L1 Cleared', 'L2 Cleared', 'L3 Cleared', 'Offered'] else "#fff2f2" if entry['to_status'] == 'Rejected' else "#f8f9fa"
                            
                            st.markdown(f"""
                            <div style="
                                background-color: {status_change_color};
                                border-left: 4px solid #28a745;
                                padding: 10px;
                                margin: 5px 0;
                                border-radius: 4px;
                            ">
                                <strong>📅 {entry['timestamp']}</strong><br>
                                <span style="color: #666;">From:</span> {entry['from_status']} 
                                <span style="color: #666;">→ To:</span> <strong>{entry['to_status']}</strong><br>
                                {f"<span style='color: #666; font-style: italic;'>Notes: {entry['notes']}</span>" if entry['notes'] else ""}
                            </div>
                            """, unsafe_allow_html=True)
                    else:
                        st.info("No status changes recorded yet.")
    else:
        # Show specific candidate history
        candidate = next((c for c in candidate_profiles if c['candidate_name'] == selected_candidate), None)
        if candidate:
            candidate_id = candidate['id']
            current_status = st.session_state.candidate_statuses.get(candidate_id, "Screening")
            
            st.markdown(f"### 👤 {candidate['candidate_name']}")
            st.markdown(f"**Current Status:** {current_status}")
            st.markdown(f"**Domain:** {candidate['domain']} | **Experience:** {candidate['experience_years']} years")
            
            if candidate_id in st.session_state.status_history:
                history = st.session_state.status_history[candidate_id]
                
                if history:
                    st.markdown("#### 📜 Status Change Timeline")
                    for i, entry in enumerate(reversed(history)):
                        is_positive = entry['to_status'] in ['L1 Cleared', 'L2 Cleared', 'L3 Cleared', 'Offered']
                        is_negative = entry['to_status'] == 'Rejected'
                        
                        if is_positive:
                            st.success(f"✅ **{entry['timestamp']}**: {entry['from_status']} → **{entry['to_status']}**" + 
                                     (f"\n💬 *{entry['notes']}*" if entry['notes'] else ""))
                        elif is_negative:
                            st.error(f"❌ **{entry['timestamp']}**: {entry['from_status']} → **{entry['to_status']}**" +
                                   (f"\n💬 *{entry['notes']}*" if entry['notes'] else ""))
                        else:
                            st.info(f"🔄 **{entry['timestamp']}**: {entry['from_status']} → **{entry['to_status']}**" +
                                  (f"\n💬 *{entry['notes']}*" if entry['notes'] else ""))
                else:
                    st.info("No status changes recorded yet.")
    
    # Analytics section
    st.divider()
    st.subheader("📊 Pipeline Analytics")
    
    # Status distribution chart
    status_counts = {}
    for status_info in INTERVIEW_STATUSES:
        count = len(get_candidates_by_status(status_info["name"]))
        if count > 0:
            status_counts[f"{status_info['icon']} {status_info['name']}"] = count
    
    if status_counts:
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### 🥧 Status Distribution")
            st.bar_chart(status_counts)
        
        with col2:
            st.markdown("#### 📈 Quick Stats")
            total = sum(status_counts.values())
            
            if total > 0:
                success_rate = (status_counts.get("🏆 L3 Cleared", 0) + status_counts.get("💼 Offered", 0)) / total * 100
                rejection_rate = status_counts.get("❌ Rejected", 0) / total * 100
                
                st.metric("Success Rate", f"{success_rate:.1f}%")
                st.metric("Rejection Rate", f"{rejection_rate:.1f}%")
                st.metric("Conversion Rate (L1→Offer)", f"{success_rate:.1f}%")

def display_qa_section(title, content, icon="📝"):
    """Display Q&A content in interviewer-friendly format with robust parsing"""
    st.subheader(f"{icon} {title}")
    
    # Debug option - show as a checkbox instead of nested expander
    show_debug = st.checkbox("🐛 Show Raw Content (Debug)", key=f"debug_{title}")
    if show_debug:
        st.code(content, language="text")
    
    # Try to split by **Q:** first
    if '**Q:**' in content:
        sections = content.split('**Q:**')
    else:
        # Fallback: just display the content as-is
        st.markdown(content)
        return
    
    for i, section in enumerate(sections[1:], 1):  # Skip first empty split
        if not section.strip():
            continue
            
        with st.expander(f"Question {i}", expanded=True):
            # Parse the Q&A format
            lines = [line.strip() for line in section.strip().split('\n') if line.strip()]
            if not lines:
                continue
                
            question = lines[0].strip()
            st.markdown(f"**❓ Question:** {question}")
            
            # Parse other components with more flexible matching
            current_section = ""
            current_content = []
            
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue
                    
                # Check for various answer formats
                if any(marker in line for marker in ['**Expected Answer:**', '**Answer:**', '**Good Answer Should Include:**']):
                    if current_section and current_content:
                        st.markdown(f"**{current_section}:** {' '.join(current_content)}")
                    current_section = "✅ Expected Answer"
                    # Extract content after the marker
                    for marker in ['**Expected Answer:**', '**Answer:**', '**Good Answer Should Include:**']:
                        if marker in line:
                            current_content = [line.replace(marker, '').strip()]
                            break
                    else:
                        current_content = [line]
                        
                elif any(marker in line for marker in ['**Red Flag:**', '**Warning Signs:**']):
                    if current_section and current_content:
                        st.markdown(f"**{current_section}:** {' '.join(current_content)}")
                    current_section = "🚩 Warning Signs"
                    for marker in ['**Red Flag:**', '**Warning Signs:**']:
                        if marker in line:
                            current_content = [line.replace(marker, '').strip()]
                            break
                    else:
                        current_content = [line]
                        
                elif any(marker in line for marker in ['**Follow-up:**', '**Probe Further:**']):
                    if current_section and current_content:
                        st.markdown(f"**{current_section}:** {' '.join(current_content)}")
                    current_section = "🔍 Follow-up"
                    for marker in ['**Follow-up:**', '**Probe Further:**']:
                        if marker in line:
                            current_content = [line.replace(marker, '').strip()]
                            break
                    else:
                        current_content = [line]
                else:
                    current_content.append(line)
            
            # Display the last section
            if current_section and current_content:
                st.markdown(f"**{current_section}:** {' '.join(current_content)}")

def display_coding_problems(problems_content, selected_language):
    """Display coding problems with solutions in selected language"""
    st.subheader(f"💻 Coding Problems - {selected_language}")
    
    # Debug option
    show_debug = st.checkbox("🐛 Show Raw Content (Debug)", key="debug_coding")
    if show_debug:
        st.code(problems_content, language="text")
    
    # Split by problem markers - look for **Problem X:** pattern
    import re
    problem_sections = re.split(r'\*\*Problem \d+:\*\*', problems_content)
    
    # Remove empty first section if it exists
    if problem_sections and not problem_sections[0].strip():
        problem_sections = problem_sections[1:]
    
    if not problem_sections:
        st.markdown(problems_content)
        return
    
    for i, section in enumerate(problem_sections, 1):
        if not section.strip():
            continue
            
        with st.expander(f"💻 Coding Problem {i}", expanded=True):
            lines = section.strip().split('\n')
            
            current_section = ""
            current_content = []
            code_block = []
            in_code_block = False
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Check for code block markers
                if line.startswith('```'):
                    if in_code_block:
                        # End of code block - display the code
                        if code_block:
                            st.markdown(f"**💡 {selected_language} Solution:**")
                            language_hint = selected_language.lower()
                            if language_hint == "c++":
                                language_hint = "cpp"
                            elif language_hint == "c#":
                                language_hint = "csharp"
                            st.code('\n'.join(code_block), language=language_hint)
                        code_block = []
                        in_code_block = False
                    else:
                        # Start of code block
                        if current_section and current_content:
                            st.markdown(f"**{current_section}:** {' '.join(current_content)}")
                            current_content = []
                        in_code_block = True
                    continue
                
                if in_code_block:
                    code_block.append(line)
                    continue
                
                # Parse different sections using ** markers
                if line.startswith('**Problem Statement:**'):
                    if current_section and current_content:
                        st.markdown(f"**{current_section}:** {' '.join(current_content)}")
                    current_section = "📋 Problem Statement"
                    current_content = [line.replace('**Problem Statement:**', '').strip()]
                    
                elif line.startswith('**Input:**'):
                    if current_section and current_content:
                        st.markdown(f"**{current_section}:** {' '.join(current_content)}")
                    current_section = "📥 Input"
                    current_content = [line.replace('**Input:**', '').strip()]
                    
                elif line.startswith('**Output:**'):
                    if current_section and current_content:
                        st.markdown(f"**{current_section}:** {' '.join(current_content)}")
                    current_section = "📤 Output"
                    current_content = [line.replace('**Output:**', '').strip()]
                    
                elif line.startswith(f'**{selected_language} Solution:**'):
                    if current_section and current_content:
                        st.markdown(f"**{current_section}:** {' '.join(current_content)}")
                    current_section = ""
                    current_content = []
                    # This will be handled by code block logic
                    
                elif line.startswith('**Explanation:**'):
                    if current_section and current_content:
                        st.markdown(f"**{current_section}:** {' '.join(current_content)}")
                    current_section = "💡 Explanation"
                    current_content = [line.replace('**Explanation:**', '').strip()]
                    
                elif line.startswith('**Time Complexity:**'):
                    if current_section and current_content:
                        st.markdown(f"**{current_section}:** {' '.join(current_content)}")
                    current_section = "⏱️ Time Complexity"
                    current_content = [line.replace('**Time Complexity:**', '').strip()]
                    
                else:
                    if line.strip():
                        current_content.append(line)
            
            # Display the last section
            if current_section and current_content:
                st.markdown(f"**{current_section}:** {' '.join(current_content)}")

# Simplified Question Generator for Interviewer Quick Prep
class InterviewerPrepGenerator:
    def generate_quick_brief(self, candidate_data):
        """Generate a concise interviewer brief"""
        prompt = (
            f"Generate a concise interviewer preparation brief for:\n\n"
            f"CANDIDATE: {candidate_data.get('Full Name', 'Unknown')}\n"
            f"DOMAIN: {candidate_data.get('Relevant Domain', 'General')}\n"
            f"EXPERIENCE: {candidate_data.get('Years of Experience', 0)} years\n"
            f"KEY SKILLS: {', '.join(candidate_data.get('Skills', [])[:5])}\n"
            f"PROJECTS: {', '.join(candidate_data.get('Projects', [])[:2])}\n\n"
            
            "Provide:\n\n"
            "CANDIDATE SUMMARY:\n"
            "Brief 2-3 line summary of candidate profile\n\n"
            
            "KEY AREAS TO ASSESS:\n"
            "List 3-4 main areas to focus on during interview\n\n"
            
            "EXPERIENCE LEVEL EXPECTATION:\n"
            "What to expect from someone with this experience level\n\n"
            
            "Keep it concise and actionable for interviewer quick prep."
        )
        return self._call_openai(prompt, max_tokens=800)
    
    def generate_quick_assessment_qa(self, domain, skills, experience):
        """Generate 5-minute assessment questions WITH answers"""
        prompt = (
            f"Generate 5 quick assessment questions for {domain} candidate ({experience} years experience).\n"
            f"Skills: {', '.join(skills[:5]) if skills else 'Basic skills'}\n\n"
            
            "For each question, use EXACTLY this format:\n\n"
            "Q:** [Your question here]\n"
            "Expected Answer:** [What a good candidate should say]\n"
            "Red Flag:** [Concerning responses to watch for]\n"
            "Follow-up:** [If you need to dig deeper]\n\n"
            
            "Focus on questions that quickly reveal:\n"
            "- Actual understanding vs resume claims\n"
            "- Communication skills\n"
            "- Problem-solving approach\n"
            "- Technical competency\n\n"
            
            "Make questions practical and easy to evaluate answers. Use the exact format above."
        )
        return self._call_openai(prompt, max_tokens=1400)
    
    def generate_coding_problems(self, domain, skills, experience, programming_language):
        """Generate coding problems with solutions in specified language"""
        
        # Determine difficulty based on experience
        if experience <= 2:
            difficulty = "Easy to Medium"
            complexity_note = "Focus on basic programming concepts, loops, conditions, and simple data structures"
        elif experience <= 5:
            complexity_note = "Include algorithms, data structures, and problem-solving skills"
            difficulty = "Medium"
        else:
            difficulty = "Medium to Hard"
            complexity_note = "Include advanced algorithms, optimization, and system design concepts"
        
        prompt = (
            f"Generate 3 coding problems for a {domain} candidate with {experience} years experience.\n"
            f"Skills: {', '.join(skills[:5]) if skills else 'General programming'}\n"
            f"Programming Language: {programming_language}\n"
            f"Difficulty Level: {difficulty}\n\n"
        
            f"For each problem, use EXACTLY this format:\n\n"
            f"**Problem 1:**\n"
            f"**Problem Statement:** [Clear problem description]\n"
            f"**Input:** [Sample input format]\n"
            f"**Output:** [Expected output format]\n"
            f"**{programming_language} Solution:**\n"
            f"```{programming_language.lower()}\n"
            f"[Complete working code solution]\n"
            f"```\n"
            f"**Explanation:** [Brief explanation of approach]\n"
            f"**Time Complexity:** [Big O notation]\n\n"
        
            f"Requirements:\n"
            f"- {complexity_note}\n"
            f"- Problems should be solvable in 15-30 minutes each\n"
            f"- Include complete, working code solutions in {programming_language}\n"
            f"- Make problems relevant to {domain} if possible\n"
            f"- Provide clear input/output examples\n"
            f"- Include time complexity analysis\n\n"
        
            f"Use the exact format above for all 3 problems."
        )
        return self._call_openai(prompt, max_tokens=2000)
    
    def _call_openai(self, prompt, max_tokens=1000):
        try:
            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.3
            )
            result = response.choices[0].message.content.strip()
            print(f"OpenAI Response (first 200 chars): {result[:200]}...")
            return result
        except Exception as e:
            error_msg = f"Error generating content: {str(e)}"
            print(error_msg)
            return error_msg

    def judge_llm_self_evaluation(self, llm_outputs_dict):
        """
        Use LLM to rate the overall quality of its own generated responses for a candidate (Q&A, coding, brief, etc).
        llm_outputs_dict: dict of all LLM outputs (brief, questions/answers, coding solutions, etc)
        Returns a dict: {metric: score, ...}
        """
        outputs_text = "\n\n".join([
            f"{key}:\n{value}" for key, value in llm_outputs_dict.items() if value and isinstance(value, str) and value.strip()])
        if not outputs_text.strip():
            return {"error": "No LLM outputs to evaluate."}
        prompt = (
            "You are an expert LLM evaluator. Below are all the responses generated by an OpenAI LLM for a candidate in an interview prep application.\n"
            "Evaluate the overall quality of these LLM-generated responses.\n"
            "Rate EACH metric from 1 (poor) to 5 (excellent):\n"
            "- Accuracy: Are the responses correct and reliable?\n"
            "- Helpfulness: Do the responses provide valuable and actionable information?\n"
            "- Relevance: Are the responses on-topic and appropriate?\n"
            "- Clarity: Are the responses clear and easy to understand?\n"
            "\nRespond strictly in valid JSON like this:\n"
            "{\"Accuracy\": 4, \"Helpfulness\": 5, \"Relevance\": 4, \"Clarity\": 5}\n"
            f"\nLLM-GENERATED RESPONSES:\n{outputs_text}\n"
        )
        try:
            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.1
            )
            content = response.choices[0].message.content.strip()
            json_start = content.find("{")
            json_end = content.rfind("}")
            if json_start == -1 or json_end == -1:
                return {"error": "LLM did not return JSON"}
            metrics = json.loads(content[json_start:json_end+1])
            return metrics
        except Exception as e:
            return {"error": str(e)}
            return error_msg

def extract_text_from_pdf(file):
    try:
        reader = PdfReader(file)
        text = "\n".join(page.extract_text() for page in reader.pages if page.extract_text())
        return text.strip() if text else None
    except Exception as e:
        return f"Error extracting text: {str(e)}"

def parse_resume_with_gpt(resume_text):
    prompt = (
        "You are a meticulous resume parser AI. Extract ONLY and ALL of the following details in strict JSON format:\n"
        "- Full Name (string)\n"
        "- Skills (list of strings)\n"
        "- Years of Experience (integer)\n"
        "- Relevant Domain (string)\n"
        "- GitHub Links (list of URLs)\n"
        "- LinkedIn Links (list of URLs)\n"
        "- Projects (list of short descriptions; if not present, empty list)\n"
        "- Past Job Titles (list of strings; if not present, empty list)\n\n"
        "Return strictly valid JSON ONLY. No explanations.\n"
        f"Resume text:\n{resume_text}"
    )

    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.2
        )
        response_text = response.choices[0].message.content.strip()
        json_start = response_text.find("{")
        json_end = response_text.rfind("}")
        if json_start == -1 or json_end == -1:
            return {"error": "AI response is not in JSON format."}
        json_text = response_text[json_start:json_end+1]
        parsed_data = json.loads(json_text)

        required_keys = ["Full Name", "Skills", "Years of Experience", "Relevant Domain", "GitHub Links", "LinkedIn Links", "Projects", "Past Job Titles"]
        for key in required_keys:
            if key not in parsed_data:
                if key in ["Skills", "GitHub Links", "LinkedIn Links", "Projects", "Past Job Titles"]:
                    parsed_data[key] = []
                else:
                    return {"error": f"Missing key in AI response: {key}"}

        return parsed_data
    except json.JSONDecodeError:
        return {"error": "Could not parse JSON from AI response."}
    except Exception as e:
        return {"error": str(e)}

current_candidate_id = 1
candidate_profiles = []

def save_candidate_profile(parsed_details, resume_filename):
    global current_candidate_id
    try:
        # Save to in-memory list
        candidate = {
            'id': current_candidate_id,
            'candidate_name': parsed_details.get('Full Name', ''),
            'resume_filename': resume_filename,
            'github_links': parsed_details.get('GitHub Links', []),
            'linkedin_links': parsed_details.get('LinkedIn Links', []),
            'domain': parsed_details.get('Relevant Domain', 'General'),
            'experience_years': parsed_details.get('Years of Experience', 0),
            'skills': parsed_details.get('Skills', []),
            'projects': parsed_details.get('Projects', []),
            'job_titles': parsed_details.get('Past Job Titles', []),
            'created_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        candidate_profiles.append(candidate)
        current_candidate_id += 1
        return candidate['id']
    except Exception as e:
        st.error(f"Error saving candidate profile: {str(e)}")
        return None

def test_aws_credentials():
    """Test AWS credentials and S3 connectivity"""
    try:
        # Try to create S3 client with explicit error handling
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-east-1')
        )
        
        # Test connectivity by listing buckets
        response = s3_client.list_buckets()
        return True, "AWS credentials are working correctly"
        
    except NoCredentialsError:
        return False, "AWS credentials not found. Please check your .env file or environment variables."
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'InvalidAccessKeyId':
            return False, "Invalid AWS Access Key ID"
        elif error_code == 'SignatureDoesNotMatch':
            return False, "Invalid AWS Secret Access Key"
        else:
            return False, f"AWS Error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def list_s3_resumes():
    """List all PDF files in the S3 bucket with improved error handling"""
    try:
        bucket_name = os.getenv('S3_BUCKET_NAME', 'resumefolderbucket')
        
        # Test credentials first
        is_valid, message = test_aws_credentials()
        if not is_valid:
            st.error(f"AWS Credentials Error: {message}")
            return []
        
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-east-1')
        )
        
        response = s3_client.list_objects_v2(Bucket=bucket_name)
        if 'Contents' not in response:
            st.warning(f"No files found in S3 bucket '{bucket_name}'.")
            return []
            
        # Filter for PDF files and sort by last modified date (newest first)
        resumes = [obj['Key'] for obj in sorted(
            response['Contents'], 
            key=lambda x: x['LastModified'], 
            reverse=True
        ) if obj['Key'].lower().endswith('.pdf')]
        
        return resumes
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'NoSuchBucket':
            st.error(f"S3 bucket '{bucket_name}' does not exist.")
        elif error_code == 'AccessDenied':
            st.error("Access denied to S3 bucket. Please check your permissions.")
        else:
            st.error(f"S3 Error: {str(e)}")
        return []
    except Exception as e:
        st.error(f"Error listing resumes from S3: {str(e)}")
        return []

def save_feedback_to_s3(assessment_data):
    import botocore
    try:
        s3 = boto3.client('s3',
                         aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                         aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                         region_name=os.getenv('AWS_DEFAULT_REGION'))
        
        bucket_name = os.getenv('S3_BUCKET_FEEDBACK')
        if not bucket_name:
            return False, "S3_BUCKET_FEEDBACK environment variable is not set."
        feedback_key = "feedback/interview_feedback.xlsx"
        
        # Flatten the ratings dictionary for Excel and safely handle None values
        ratings = assessment_data.get('ratings', {})
        new_row = {
            'candidate_id': assessment_data.get('candidate_id', ''),
            'candidate_name': assessment_data.get('candidate_name', ''),
            'candidate_status': assessment_data.get('candidate_status', ''),
            'timestamp': assessment_data.get('timestamp', ''),
            'technical_rating': ratings.get('technical', ''),
            'communication_rating': ratings.get('communication', ''),
            'problem_solving_rating': ratings.get('problem_solving', ''),
            'culture_fit_rating': ratings.get('culture_fit', ''),
            'coding_rating': ratings.get('coding', ''),
            'strengths': (assessment_data.get('strengths') or '').replace('\n', ' ').replace('\r', ' '),
            'concerns': (assessment_data.get('concerns') or '').replace('\n', ' ').replace('\r', ' '),
            'coding_feedback': (assessment_data.get('coding_feedback') or '').replace('\n', ' ').replace('\r', ' '),
            'decision': assessment_data.get('decision', ''),
            'notes': (assessment_data.get('notes') or '').replace('\n', ' ').replace('\r', ' ')
        }
        
        from io import BytesIO
        
        # Try to download existing file from S3
        try:
            response = s3.get_object(Bucket=bucket_name, Key=feedback_key)
            existing_df = pd.read_excel(BytesIO(response['Body'].read()))
            updated_df = pd.concat([existing_df, pd.DataFrame([new_row])], ignore_index=True)
        except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                updated_df = pd.DataFrame([new_row])
            else:
                raise
        
        # Save to Excel in memory
        excel_buffer = BytesIO()
        updated_df.to_excel(excel_buffer, index=False, engine='openpyxl')
        excel_bytes = excel_buffer.getvalue()
        if not excel_bytes:
            return False, "Excel buffer is empty. Nothing to upload."
        
        # Debug print
        print(f"bucket_name: {bucket_name}, feedback_key: {feedback_key}, excel_bytes type: {type(excel_bytes)}, excel_bytes is None: {excel_bytes is None}")
        
        # Upload to S3
        s3.put_object(
            Bucket=bucket_name,
            Key=feedback_key,
            Body=excel_bytes
        )
        return True, "Feedback saved successfully to the shared Excel file in S3"
    except Exception as e:
        return False, f"Error saving to S3: {str(e)}"

def download_resume_from_s3(key):
    """Download a file from S3 and return a file-like object"""
    try:
        bucket_name = os.getenv('S3_BUCKET_NAME', 'resumefolderbucket')
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-east-1')
        )
        
        response = s3_client.get_object(Bucket=bucket_name, Key=key)
        return io.BytesIO(response['Body'].read())
    except Exception as e:
        st.error(f"Error downloading {key} from S3: {str(e)}")
        return None

# --- Streamlined Streamlit UI ---
st.set_page_config(
    page_title="🎯 Interviewer Quick Prep",
    page_icon="👥",
    layout="wide"
)

# Initialize session state
initialize_session_state()

# Custom CSS for clean interviewer design
st.markdown("""
<style>
    .interviewer-header {
        font-size: 2.5rem;
        font-weight: bold;
        text-align: center;
        background: linear-gradient(90deg, #2E8B57 0%, #228B22 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 1rem;
    }
    .candidate-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 1.5rem;
        border-radius: 15px;
        margin: 1rem 0;
    }
    .prep-section {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 1rem;
        margin: 1rem 0;
        border-left: 4px solid #28a745;
    }
    .language-selector {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 1rem;
        border-radius: 10px;
        margin: 1rem 0;
    }
    .error-box {
        background-color: #f8d7da;
        border: 1px solid #f5c6cb;
        color: #721c24;
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
    }
    .success-box {
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        color: #155724;
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
    }
    .status-info {
        background-color: #e3f2fd;
        border: 1px solid #90caf9;
        color: #0d47a1;
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# Main header
st.markdown('<h1 class="interviewer-header"> Interview Edge</h1>', unsafe_allow_html=True)

st.markdown("#### <span style='color:#4F8BF9;font-weight:bold;'>How would you like to provide the resume?</span>", unsafe_allow_html=True)
col1, col2 = st.columns(2)
with col1:
    if st.button("upload from local files", use_container_width=True):
        st.session_state.resume_input_method = "upload"
with col2:
    if st.button("☁️ Select from NexTurn datastore", use_container_width=True):
        st.session_state.resume_input_method = "s3"

# Set a default if not set
if "resume_input_method" not in st.session_state:
    st.session_state.resume_input_method = None

selection_method = st.session_state.resume_input_method

uploaded_file = None

if selection_method == "upload":
    # File upload option
    uploaded_file = st.file_uploader(
        "Upload candidate resume (PDF)",
        type=['pdf'],
        help="Upload a PDF resume to generate interview questions"
    )

elif selection_method == "s3":
    # S3 selection option
    resumes = list_s3_resumes()
    if resumes:
        selected_resume = st.selectbox(
            "Choose a resume from S3 bucket",
            ["Select a resume..."] + resumes,
            index=0,
            format_func=lambda x: os.path.basename(x) if x != "Select a resume..." else x,
            help="Select a resume to generate interview questions and coding problems"
        )
        if selected_resume and selected_resume != "Select a resume...":
            with st.spinner(f"Downloading {selected_resume}..."):
                uploaded_file = download_resume_from_s3(selected_resume)
                if uploaded_file:
                    uploaded_file.name = os.path.basename(selected_resume)
    else:
        st.warning("No resumes found in S3 bucket or unable to connect to S3.")
        st.info("💡 Try using the 'Upload PDF File' option instead.")

# Process the resume if available
if uploaded_file:
    # Simple progress
    with st.spinner("🔍 Analyzing resume and preparing interview materials..."):
        resume_text = extract_text_from_pdf(uploaded_file)
    
    if resume_text and not resume_text.startswith("Error"):
        with st.spinner("🎯 Generating questions with answers..."):
            parsed_details = parse_resume_with_gpt(resume_text)

        if isinstance(parsed_details, dict) and "error" not in parsed_details:
            # Check candidate status in S3 CSV file
            candidate_name = parsed_details.get('Full Name', '')
            if candidate_name:
                candidate_status, status_message = check_candidate_status_in_s3_csv(candidate_name)
                # Suggest next round based on candidate_status
                next_round_message = ""
                # Determine the interview round
                if candidate_status.startswith("L1"):
                    interview_round = "L1"
                elif candidate_status.startswith("L2"):
                    interview_round = "L2"
                elif candidate_status.startswith("L3"):
                    interview_round = "L3"
                else:
                    interview_round = "L1"  # Default fallback

                if interview_round == "L1":
                    next_round_message = "You have to take the L2 round for this candidate."
                elif interview_round == "L2":
                    next_round_message = "You have to take the L3 round for this candidate."
                elif interview_round == "L3":
                    next_round_message = "All rounds completed. You can proceed to feedback or offer."
                else:
                    next_round_message = "Candidate status not recognized. Please go with the L1 round for this candidate."
                experience = parsed_details.get('Years of Experience', 0)
                skills = parsed_details.get('Skills', [])
                questions, coding_problems = generate_questions_and_coding(interview_round, experience, skills)

                st.info(f"🔔 {next_round_message}")
                
                # Display candidate status information
                if candidate_status == "Need to go with L1":
                    st.markdown(f"""
                    <div class="status-info">
                        📋 <strong>Candidate Status:</strong> {candidate_status}<br>
                        <small>ℹ️ {status_message}</small>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div class="status-info">
                        📊 <strong>Current Candidate Status:</strong> {candidate_status}<br>
                        <small>✅ {status_message}</small>
                    </div>
                    """, unsafe_allow_html=True)
            
            # Save candidate profile
            candidate_id = save_candidate_profile(parsed_details, uploaded_file.name)
            
            # Initialize candidate status if not exists
            if candidate_id not in st.session_state.candidate_statuses:
                st.session_state.candidate_statuses[candidate_id] = "Screening"
            
            # Generate interviewer preparation content
            prep_generator = InterviewerPrepGenerator()
            
            st.success("✅ Interview preparation ready!")
            
            # Candidate overview card
            st.markdown(f"""
            <div class="candidate-card">
                <h2>👤 {parsed_details['Full Name']}</h2>
                <div style="display: flex; justify-content: space-between; margin-top: 1rem;">
                    <div><strong>Domain:</strong> {parsed_details['Relevant Domain']}</div>
                    <div><strong>Experience:</strong> {parsed_details['Years of Experience']} years</div>
                    <div><strong>Key Skills:</strong> {', '.join(parsed_details['Skills'][:3])}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Updated tabs - Added LLM Metrics tab
            tabs = st.tabs([
                "📋 Quick Brief", 
                "⚡ 5-Min Assessment Q&A", 
                "💻 Quick Coding Q&A",
                "📝 Feedback",
                "📊 LLM Metrics"
            ])

            # Quick Brief Tab
            with tabs[0]:
                # st.header("📋 Candidate Brief")
                try:
                    brief = prep_generator.generate_quick_brief(parsed_details)
                    
                    st.markdown(f"""
                    <div class="prep-section">
                        {brief.replace(chr(10), '<br>')}
                    </div>
                    """, unsafe_allow_html=True)
                except Exception as e:
                    st.error(f"Error generating brief: {str(e)}")
                
                # Quick reference info
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("#### Skills to Validate")
                    for skill in parsed_details['Skills'][:5]:
                        st.markdown(f"• {skill}")
                    
                    if parsed_details['GitHub Links']:
                        st.markdown("#### GitHub Profile")
                        for link in parsed_details['GitHub Links']:
                            st.markdown(f"• [View Profile]({link})")
                
                with col2:
                    st.markdown("#### Background")
                    st.markdown(f"• **Experience:** {parsed_details['Years of Experience']} years")
                    st.markdown(f"• **Projects:** {len(parsed_details['Projects'])} listed")
                    if parsed_details['Past Job Titles']:
                        st.markdown("• **Past Roles:**")
                        for role in parsed_details['Past Job Titles'][:3]:
                            st.markdown(f"  - {role}")

            # 5-Min Assessment Tab with Q&A
            with tabs[1]:
                # st.header("⚡ 5-Minute Quick Assessment")
                st.info("💡 Start with these questions to quickly gauge the candidate. Please note that answers should be concise and direct.")
                
                try:
                    st.markdown("### ⚡ Quick Assessment Q&A")
                    for idx, (q, a) in enumerate(questions, 1):
                        st.markdown(
                            f"""
                            <div style="margin-bottom: 1.5em; padding: 1em; border-radius: 8px; background: #f8f9fa; box-shadow: 0 1px 2px rgba(0,0,0,0.03);">
                                <div style="font-weight: bold; color: #222; margin-bottom: 0.4em;">Q{idx}: {q}</div>
                                <div style="margin-left: 1em; color: #444;"><span style="color: #009688; font-weight: 500;">A:</span> {a}</div>
                            </div>
                            """,
                            unsafe_allow_html=True
                        )
                except Exception as e:
                    st.error(f"Error generating quick assessment: {str(e)}")
                    st.info("Please try refreshing the page or check your OpenAI API key.")

            # Quick Coding Q&A Tab
            with tabs[2]:
                st.markdown("### 💻 Quick Coding Problems")
                for idx, (q, a) in enumerate(coding_problems, 1):
                    with st.expander(f"Problem {idx}", expanded=True):
                        st.markdown(f"**Question:** {q}")
                        
                        # Parse and display the answer with code blocks
                        import re
                        code_blocks = re.findall(r"```(?:[a-zA-Z0-9]*)\n?(.*?)```", a, re.DOTALL)
                        if code_blocks:
                            # Display text before code
                            pre_code = a.split("```")[0]
                            if pre_code.strip():
                                st.markdown(f"**Solution:** {pre_code.strip()}")
                            
                            # Display code blocks
                            for code in code_blocks:
                                st.code(code.strip(), language="python")
                            
                            # Display text after code
                            post_code_parts = a.split("```")
                            if len(post_code_parts) > 2 and post_code_parts[-1].strip():
                                st.markdown(post_code_parts[-1].strip())
                        else:
                            st.markdown(f"**Solution:** {a}")

                
                # Common programming languages
                programming_languages = [
                    "Python", "Java", "JavaScript", "C++"
                ]

                # Initialize language session state if not present
                if 'selected_language' not in st.session_state:
                    st.session_state.selected_language = programming_languages[0]
                if 'prev_selected_language' not in st.session_state:
                    st.session_state.prev_selected_language = programming_languages[0]
                
                st.markdown("<div style='display: flex; justify-content: flex-end; margin-bottom: 0.5em;'><span style='font-weight: 600; margin-right: 0.5em;'>Language:</span></div>", unsafe_allow_html=True)
                selected_language = st.selectbox(
                    "",
                    programming_languages,
                    index=programming_languages.index(st.session_state.get('selected_language', programming_languages[0])),
                    key="language_selector"
                )

                # --- Caching logic for coding problems by language ---
                if 'coding_cache' not in st.session_state:
                    st.session_state.coding_cache = {}
                # Reset coding problems if language changed (but keep cache)
                if selected_language != st.session_state.prev_selected_language:
                    st.session_state.coding_problems = None
                    st.session_state.selected_language = selected_language
                    st.session_state.prev_selected_language = selected_language
                # Generate coding problems button (only if not cached)
                if st.button(f"🚀 Generate {selected_language} Coding Problems", type="primary"):
                    if selected_language in st.session_state.coding_cache:
                        st.session_state.coding_problems = st.session_state.coding_cache[selected_language]
                        st.session_state.selected_language = selected_language
                        st.session_state.prev_selected_language = selected_language
                    else:
                        with st.spinner(f"🔧 Generating {selected_language} coding problems..."):
                            try:
                                coding_problems = prep_generator.generate_coding_problems(
                                    parsed_details.get("Relevant Domain", "General"),
                                    parsed_details.get("Skills", []),
                                    parsed_details.get("Years of Experience", 0),
                                    selected_language
                                )
                                st.session_state.coding_problems = coding_problems
                                st.session_state.selected_language = selected_language
                                st.session_state.prev_selected_language = selected_language
                                st.session_state.coding_cache[selected_language] = coding_problems
                            except Exception as e:
                                st.error(f"Error generating coding problems: {str(e)}")
                                st.info("Please try refreshing the page or check your OpenAI API key.")
                # If problems are cached for current language, display them
                if not st.session_state.get('coding_problems') and selected_language in st.session_state.coding_cache:
                    st.session_state.coding_problems = st.session_state.coding_cache[selected_language]
                # Display coding problems if they exist in session state and match current language
                if st.session_state.get('coding_problems') and st.session_state.get('selected_language') == selected_language:
                    st.success(f"✅ {selected_language} coding problems generated!")
                    display_coding_problems(st.session_state.coding_problems, selected_language)
                    with st.expander("💡 Interview Tips for Coding Assessment", expanded=False):
                        st.markdown("""
                        **🎯 What to Look For:**
                        - **Problem Understanding**: Does candidate ask clarifying questions?
                        - **Approach**: Can they explain their solution strategy before coding?
                        - **Code Quality**: Clean, readable, and well-structured code
                        - **Testing**: Do they consider edge cases and test scenarios?
                        - **Communication**: Can they explain their thought process clearly?
                        
                        **⏱️ Time Management:**
                        - Give 15-30 minutes per problem depending on complexity
                        - Allow candidate to choose their preferred problem if time is limited
                        - Focus on problem-solving approach rather than perfect syntax
                        
                        **🤔 Follow-up Questions:**
                        - "How would you optimize this solution?"
                        - "What would happen with very large inputs?"
                        - "Can you think of alternative approaches?"
                        """)

            # Feedback Tab
            with tabs[3]:
                # st.header("📝 Feedback")
                
                with st.form("interview_notes"):
                    st.markdown("###  Interview Assessment")
                    
                    # Candidate Status Dropdown
                    candidate_status = st.selectbox(
                        "Candidate Status",
                        ["L1 completed", "L2 completed", "L3 completed"],
                        index=0,  # Default to L1
                        help="Select the interview round/level for this candidate"
                    )
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        technical_rating = st.slider("Technical Skills (1-5)", 1, 5, 3)
                        communication_rating = st.slider("Communication (1-5)", 1, 5, 3)
                    with col2:
                        problem_solving = st.slider("Problem Solving (1-5)", 1, 5, 3)
                        culture_fit = st.slider("Culture Fit (1-5)", 1, 5, 3)
                    
                    # Coding assessment rating
                    coding_rating = st.slider("Coding Skills (1-5)", 1, 5, 3)
                    
                    # Key observations
                    st.markdown("### 📝 Key Observations")
                    strengths = st.text_area("Candidate Strengths:")
                    concerns = st.text_area("Areas of Concern:")
                    coding_feedback = st.text_area("Coding Assessment Feedback:")
                    
                    # Final Decision Score
                    final_decision = st.slider(
                        "Final Decision Score (1-5)",
                        1, 5, 3,
                        help="Rate your overall final decision for this candidate"
                    )
                    
                    # Additional Notes
                    additional_notes = st.text_area("Additional Notes:")
        
                    
                    if st.form_submit_button("💾 Save Interview Assessment", type="primary"):
                        try:
                            assessment_data = {
                                "candidate_id": candidate_id,
                                "candidate_name": parsed_details.get('Full Name', 'Unknown'),
                                "candidate_status": candidate_status,  # Add the candidate status here
                                "ratings": {
                                    "technical": technical_rating,
                                    "communication": communication_rating,
                                    "problem_solving": problem_solving,
                                    "culture_fit": culture_fit,
                                    "coding": coding_rating
                                },
                                "strengths": strengths,
                                "concerns": concerns,
                                "coding_feedback": coding_feedback,
                                "decision": final_decision,
                                "notes": additional_notes,
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }

                            # LLM judge metrics for key feedback (use coding_feedback, strengths, or concerns)
                            llm_metrics = None
                            try:
                                judge_text = coding_feedback if coding_feedback.strip() else (strengths + "\n" + concerns)
                                if judge_text.strip():
                                    if 'prep_generator' not in locals():
                                        prep_generator = InterviewerPrepGenerator()
                                    llm_metrics = prep_generator.judge_answer_llm(judge_text)
                            except Exception as e:
                                llm_metrics = {"error": str(e)}
                            assessment_data["llm_metrics"] = llm_metrics

                            # LLM self-evaluation metrics for all LLM-generated content
                            llm_self_evaluation = None
                            try:
                                llm_outputs = {}
                                # Aggregate LLM-generated outputs for this candidate/session
                                if 'brief' in locals() and brief:
                                    llm_outputs['Quick Brief'] = brief
                                if 'questions' in locals() and questions:
                                    llm_outputs['Q&A'] = '\n'.join([f"Q: {q}\nA: {a}" for q, a in questions])
                                if 'coding_problems' in locals() and coding_problems:
                                    llm_outputs['Coding'] = '\n'.join([f"{q}\n{a}" for q, a in coding_problems])
                                # Add more LLM outputs as needed
                                if llm_outputs:
                                    if 'prep_generator' not in locals():
                                        prep_generator = InterviewerPrepGenerator()
                                    llm_self_evaluation = prep_generator.judge_llm_self_evaluation(llm_outputs)
                            except Exception as e:
                                llm_self_evaluation = {"error": str(e)}
                            assessment_data["llm_self_evaluation"] = llm_self_evaluation

                            # Save to S3
                            success, message = save_feedback_to_s3(assessment_data)
                            if success:
                                st.success("✅ " + message)
                                # Also keep local copy in session state
                                if 'interview_assessments' not in st.session_state:
                                    st.session_state.interview_assessments = []
                                st.session_state.interview_assessments.append(assessment_data)
                            else:
                                st.error("❌ " + message)
                            
                            # Save to session state (since we're using in-memory storage)
                            if 'interview_assessments' not in st.session_state:
                                st.session_state.interview_assessments = []
                            
                            st.session_state.interview_assessments.append(assessment_data)
                            
                            # Auto-update status based on decision
                            status_mapping = {
                                "Strong Hire": "Offered",
                                "Hire": "L3 Cleared", 
                                "Maybe": "On Hold",
                                "No Hire": "Rejected",
                                "Strong No Hire": "Rejected"
                            }
                            
                            new_status = status_mapping.get(final_decision, "Ready for Evaluation")
                            update_candidate_status(candidate_id, new_status, f"Assessment completed: {final_decision}")
                            
                            st.success("📝 Interview assessment saved!")
                            # st.balloons()
                            
                            # Display summary
                            avg_rating = (technical_rating + communication_rating + problem_solving + culture_fit + coding_rating) / 5
                            st.markdown(f"""
                            <div class="success-box">
                                <h4>📊 Assessment Summary</h4>
                                <p><strong>Overall Rating:</strong> {avg_rating:.1f}/10</p>
                                <p><strong>Recommendation:</strong> {final_decision}</p>
                                <p><strong>Status Updated:</strong> {new_status}</p>
                                <p><strong>Assessed on:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
                            </div>
                            """, unsafe_allow_html=True)
                            
                        except Exception as e:
                            st.error(f"Error saving assessment: {str(e)}")

        else:
            st.error(f"❌ {parsed_details.get('error', 'Unable to parse resume')}")
            st.info("💡 Please ensure the resume is clear and contains readable text")

    else:
        st.error("❌ Could not extract text from the resume")

# Add a section to view saved assessments
# Add a section to view saved assessments
if 'interview_assessments' in st.session_state and st.session_state.interview_assessments:
    with st.expander("📊 View Saved Assessments", expanded=False):
        st.subheader("Previous Interview Assessments")
        
        for i, assessment in enumerate(reversed(st.session_state.interview_assessments)):
            with st.container():
                col1, col2, col3 = st.columns([2, 1, 1])
                
                with col1:
                    st.markdown(f"**{assessment['candidate_name']}**")
                    st.markdown(f"*{assessment['timestamp']}*")
                
                with col2:
                    avg_rating = sum(assessment['ratings'].values()) / len(assessment['ratings'])
                    st.metric("Overall Rating", f"{avg_rating:.1f}/10")
                
                with col3:
                    decision_color = {
                        "Strong Hire": "🟢",
                        "Hire": "🟢", 
                        "Maybe": "🟡",
                        "No Hire": "🔴",
                        "Strong No Hire": "🔴"
                    }
                    st.markdown(f"{decision_color.get(assessment['decision'], '⚪')} {assessment['decision']}")
                
                if st.button(f"View Details", key=f"view_{i}"):
                    st.json(assessment)
                # LLM Metrics Tab
            with tabs[4]:  # 5th tab (0-indexed)
                st.markdown("### 🔍 LLM Self-Evaluation Metrics")
                st.markdown("*Automatically generated evaluation of the LLM's own responses for this candidate*")
                    
                if assessment.get("llm_self_evaluation"):
                    llm_self_evaluation = assessment["llm_self_evaluation"]
                    if isinstance(llm_self_evaluation, dict):
                        # Display metrics in a grid
                        cols = st.columns(4)
                        for (metric, score), col in zip(llm_self_evaluation.items(), cols):
                            with col:
                                st.metric(
                                    label=metric,
                                    value=score,
                                    help=f"LLM self-evaluation of {metric.lower()} (1-5 scale)"
                                )
                            
                            # Add a small visualization (only if plotly is available)
                            # ... (commented plotly code)
                            
                    else:
                        st.warning("Could not parse LLM self-evaluation metrics.")
                        st.json(llm_self_evaluation)
                else:
                    st.info("No LLM self-evaluation metrics available for this assessment.")
                        
                # Add explanation about what these metrics mean
                with st.expander("ℹ️ About these metrics"):
                    st.markdown("""
                    These metrics are generated by the LLM itself, evaluating the quality of its own responses:
                    
                    - **Accuracy**: How factually correct and reliable the LLM's responses were
                    - **Helpfulness**: How useful and actionable the information provided was
                    - **Relevance**: How well the responses matched the candidate's background and role
                    - **Clarity**: How clear and easy to understand the responses were
                    
                    All scores are on a 1-5 scale, with 5 being the best possible score.
                    """)
                st.divider()


# Simple footer
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666; padding: 1rem;'>
    <p>🎯 Quick Interview Prep Tool | Get questions with answers + coding problems + status tracking</p>
    <p><small>Supports both file upload and AWS S3 integration with Kanban-style interview pipeline</small></p>
</div>
""", unsafe_allow_html=True)