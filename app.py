from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
import requests
import json
import re
import uuid
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from database import init_db, get_db_connection
import sqlite3

app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-in-production'


# Initialize Database
try:
    init_db()
except Exception as e:
    print(f"Database initialization error: {e}")

# Groq API Configuration
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

def call_groq_api(messages, temperature=0.7, max_tokens=2000):
    """Make API call to Groq with Llama 3.3"""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    try:
        response = requests.post(GROQ_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"API Error: {str(e)}")
        return None

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if 'user' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password_hash'], password):
            session['user'] = user['username']
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Invalid username or password")
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Register page"""
    if 'user' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if password != confirm_password:
            return render_template('register.html', error="Passwords do not match")
            
        conn = get_db_connection()
        try:
            # Check if user exists
            user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
            if user:
                return render_template('register.html', error="Username already exists")
                
            # Create new user
            hashed_password = generate_password_hash(password)
            conn.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                         (username, hashed_password))
            conn.commit()
            return redirect(url_for('login'))
        except Exception as e:
            return render_template('register.html', error=f"Registration error: {str(e)}")
        finally:
            conn.close()
            
    return render_template('register.html')

@app.route('/logout')
def logout():
    """Logout user"""
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    """Home page"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # Clear any existing session data specific to assessment when returning to home, 
    # but keep the user logged in.
    # We want to clear 'scenario', 'evaluation', etc.
    keys_to_remove = ['scenario', 'evaluation', 'job_role', 'user_response', 'assessment_id']
    for key in keys_to_remove:
        session.pop(key, None)
        
    return render_template('index.html', user=session.get('user'))

@app.route('/assessment')
def assessment():
    """Assessment page"""
    if 'user' not in session:
        return redirect(url_for('login'))
        
    # Check if scenario exists in session
    if 'scenario' not in session:
        return render_template('assessment.html', no_scenario=True)
    return render_template('assessment.html', no_scenario=False)

@app.route('/results')
def results():
    """Results page"""
    if 'user' not in session:
        return redirect(url_for('login'))
        
    # Check if evaluation exists in session
    if 'evaluation' not in session:
        return render_template('results.html', no_results=True)
    return render_template('results.html', no_results=False)

@app.route('/dashboard')
def dashboard():
    """User dashboard"""
    if 'user' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    try:
        user = conn.execute('SELECT id FROM users WHERE username = ?', (session['user'],)).fetchone()
        if user:
            assessments = conn.execute('''
                SELECT * FROM assessments WHERE user_id = ? ORDER BY created_at DESC
            ''', (user['id'],)).fetchall()
            
            # Convert row objects to dicts and parse JSON for template
            assessments_list = []
            for row in assessments:
                assessment = dict(row)
                try:
                    assessment['evaluation_data_json'] = json.loads(assessment['evaluation_data'])
                except:
                    assessment['evaluation_data_json'] = {}
                assessments_list.append(assessment)
                
            return render_template('dashboard.html', assessments=assessments_list)
    except Exception as e:
        print(f"Dashboard Error: {e}")
        return render_template('dashboard.html', assessments=[])
    finally:
        conn.close()
    
    return render_template('dashboard.html', assessments=[])

@app.route('/api/generate-scenario', methods=['POST'])
def generate_scenario():
    """Generate scenario based on job role"""
    data = request.json
    job_role = data.get('job_role', '')
    complexity = data.get('complexity', 'Medium')
    
    if not job_role:
        return jsonify({'error': 'Job role is required'}), 400
    
    # Define complexity-specific instructions
    complexity_instructions = ""
    if complexity == "Low":
        complexity_instructions = """
        LEVEL: LOW (Foundational Knowledge)
        - Focus on verifying core concepts, definitions, and terminology.
        - Scenario should be simple, direct, and low ambiguity.
        - Example: "What is [Concept] and why is it important?" or a very basic troubleshooting step.
        - Goal: Filter out fake credentials."""
    elif complexity == "Medium":
        complexity_instructions = """
        LEVEL: MEDIUM (Applied Thinking)
        - Focus on problem-solving, process selection, and trade-offs.
        - Scenario should be a realistic workplace situation with some ambiguity.
        - Example: "A specific problem occurred. How do you investigate and fix it?"
        - Goal: Measure real job readiness."""
    else: # High
        complexity_instructions = """
        LEVEL: HIGH (Strategic & Executive Thinking)
        - Focus on risk management, ethics, business impact, and multi-stakeholder decisions.
        - Scenario should be high-stakes, ambiguous, with no single correct answer.
        - Example: "A critical crisis with conflicting business/ethical goals. What is your strategy?"
        - Goal: Identify leaders."""

    # Create prompt for scenario generation
    prompt = f"""You are an expert in professional skills assessment. Generate a realistic scenario for a {job_role} position.
    
    {complexity_instructions}

Requirements:
1. Adhere strictly to the defined Complexity Level.
2. Include specific details relevant to the {job_role}.
3. The scenario should be 100-200 words.

Format your response as a JSON object with these fields:
{{
  "scenario_title": "Brief title of the scenario",
  "scenario_description": "Detailed scenario description",
  "complexity_level": "Medium/High",
  "key_challenges": ["challenge1", "challenge2", "challenge3"]
}}

Respond ONLY with valid JSON, no additional text."""

    messages = [
        {"role": "system", "content": "You are an expert assessment designer who creates realistic professional scenarios."},
        {"role": "user", "content": prompt}
    ]
    
    response = call_groq_api(messages, temperature=0.8)
    
    if response:
        try:
            # Clean response and parse JSON
            response = response.strip()
            if response.startswith('```json'):
                response = response[7:]
            if response.endswith('```'):
                response = response[:-3]
            response = response.strip()
            
            scenario_data = json.loads(response)
            
            # Store in session
            session['job_role'] = job_role
            session['complexity'] = complexity
            session['scenario'] = scenario_data
            session['assessment_id'] = str(uuid.uuid4())
            session.modified = True
            
            return jsonify({
                'success': True,
                'scenario': scenario_data
            })
        except json.JSONDecodeError as e:
            print(f"JSON Parse Error: {e}")
            print(f"Response: {response}")
            return jsonify({'error': 'Failed to parse scenario'}), 500
    
    return jsonify({'error': 'Failed to generate scenario'}), 500

@app.route('/api/get-scenario')
def get_scenario():
    """Get current scenario from session"""
    if 'scenario' not in session or 'job_role' not in session:
        return jsonify({'error': 'No scenario found'}), 404
    
    return jsonify({
        'success': True,
        'job_role': session.get('job_role'),
        'scenario': session.get('scenario')
    })

@app.route('/api/evaluate-response', methods=['POST'])
def evaluate_response():
    """Evaluate user's response to scenario"""
    data = request.json
    user_response = data.get('response', '')
    
    if not user_response or len(user_response) < 50:
        return jsonify({'error': 'Response too short. Please provide a detailed answer.'}), 400
    
    job_role = session.get('job_role', 'Professional')
    complexity = session.get('complexity', 'Medium')
    scenario = session.get('scenario', {})
    
    # Define evaluation criteria based on complexity
    evaluation_criteria = ""
    if complexity == "Low":
        evaluation_criteria = """
        Evaluate based on LOW Complexity (Foundational):
        1. Accuracy: Are the definitions/concepts correct?
        2. Clarity: Can the user explain it clearly?
        3. Basic Understanding: Do they grasp fundamentals?
        """
        dimensions_json = """
        "dimensions": {
            "accuracy": {"score": <0-100>, "feedback": "analysis of correctness"},
            "clarity": {"score": <0-100>, "feedback": "analysis of explanation quality"},
            "basic_understanding": {"score": <0-100>, "feedback": "grasp of core concepts"}
        }"""
    elif complexity == "Medium":
        evaluation_criteria = """
        Evaluate based on MEDIUM Complexity (Applied):
        1. Reasoning: Does the user follow a logical process?
        2. Technical Correctness: Are they choosing the right methods?
        3. Practicality: Is the solution workable?
        """
        dimensions_json = """
        "dimensions": {
            "reasoning": {"score": <0-100>, "feedback": "logic analysis"},
            "technical_correctness": {"score": <0-100>, "feedback": "method accuracy"},
            "practicality": {"score": <0-100>, "feedback": "solution feasibility"}
        }"""
    else: # High
        evaluation_criteria = """
        Evaluate based on HIGH Complexity (Strategic):
        1. Strategic Reasoning: Are long-term effects considered?
        2. Ethical Judgment: Are risks and fairness addressed?
        3. Decision Quality: Is the trade-off handled intelligently?
        """
        dimensions_json = """
        "dimensions": {
            "strategic_reasoning": {"score": <0-100>, "feedback": "long-term thinking analysis"},
            "ethical_judgment": {"score": <0-100>, "feedback": "risk/ethics analysis"},
            "decision_quality": {"score": <0-100>, "feedback": "trade-off handling"}
        }"""

    # Create evaluation prompt
    # Create evaluation prompt
    prompt = f"""You are an expert evaluator for professional capability assessment. 
    
    JOB ROLE: {job_role}
    COMPLEXITY LEVEL: {complexity}
    
    SCENARIO:
    {scenario.get('scenario_description', '')}
    
    CANDIDATE RESPONSE:
    {user_response}
    
    {evaluation_criteria}
    
    Respond ONLY with valid JSON in this exact format. Do NOT include markdown formatting or comments.
    
    CRITICAL: The "dimensions" field must be a JSON Object {{ }}, NOT a list [ ]. 
    Do NOT end the dimensions object with a square bracket ].
    
    {{
      "overall_score": <number between 0-100>,
      {dimensions_json},
      "strengths": [
        "Strength 1",
        "Strength 2",
        "Strength 3"
      ],
      "weaknesses": [
        "Weakness 1",
        "Weakness 2",
        "Weakness 3"
      ],
      "skill_readiness": "Assessment of readiness for this level",
      "recommendations": [
        "Rec 1",
        "Rec 2",
      "recommendations": [
        "Rec 1",
        "Rec 2",
        "Rec 3"
      ],
      "performace_level": "Entry/Mid/Senior/Expert",
      "ideal_answer": "A concise, high-quality, model response (100-150 words) that effectively addresses the scenario at the chosen complexity level."
    }}"""

    messages = [
        {"role": "system", "content": "You are an expert professional evaluator with deep knowledge across multiple domains."},
        {"role": "user", "content": prompt}
    ]
    
    response = call_groq_api(messages, temperature=0.5, max_tokens=2500)
    
    if response:
        try:
            # Clean response and parse JSON
            response = response.strip()
            
            # Try to find JSON block with regex
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                response = json_match.group(1)
            else:
                # If no code block, try to find the JSON object directly
                start = response.find('{')
                end = response.rfind('}')
                if start != -1 and end != -1:
                    response = response[start:end+1]
            
            evaluation_data = json.loads(response)
            
            # Save to database
            conn = get_db_connection()
            try:
                # Get user id
                user = conn.execute('SELECT id FROM users WHERE username = ?', (session['user'],)).fetchone()
                if user:
                    conn.execute('''
                        INSERT INTO assessments (user_id, job_role, complexity, overall_score, evaluation_data)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (user['id'], job_role, complexity, evaluation_data.get('overall_score', 0), json.dumps(evaluation_data)))
                    conn.commit()
            except Exception as db_err:
                 print(f"Database Save Error: {db_err}")
            finally:
                 conn.close()

            # Store in session
            session['evaluation'] = evaluation_data
            session['user_response'] = user_response
            session['timestamp'] = datetime.now().isoformat()
            session.modified = True
            
            return jsonify({
                'success': True,
                'evaluation': evaluation_data
            })
        except json.JSONDecodeError as e:
            # Attempt to fix common JSON errors
            try:
                # Fix 1: Replace matching }] with } using regex to handle whitespace
                fixed_response = re.sub(r'\]\s*\}\s*,', '}},', response)
                if fixed_response == response:
                     # Fallback for end of object
                     fixed_response = re.sub(r'\]\s*\}\s*$', '}}', response)
                
                evaluation_data = json.loads(fixed_response)
                
                # If successful, save and return
                conn = get_db_connection()
                try:
                    # Get user id
                    user = conn.execute('SELECT id FROM users WHERE username = ?', (session['user'],)).fetchone()
                    if user:
                        conn.execute('''
                            INSERT INTO assessments (user_id, job_role, complexity, overall_score, evaluation_data)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (user['id'], job_role, complexity, evaluation_data.get('overall_score', 0), json.dumps(evaluation_data)))
                        conn.commit()
                except Exception as db_err:
                    print(f"Database Save Error: {db_err}")
                finally:
                    conn.close()

                session['evaluation'] = evaluation_data
                session['user_response'] = user_response
                session['timestamp'] = datetime.now().isoformat()
                session.modified = True
                return jsonify({'success': True, 'evaluation': evaluation_data})
            except:
                pass
                
            print(f"JSON Parse Error: {e}")
            print(f"Response: {response}")
            # Log failed response for debugging
            try:
                with open("debug_evaluation_error.txt", "w", encoding='utf-8') as f:
                    f.write(f"Error: {str(e)}\n\nResponse:\n{response}")
            except:
                pass
            return jsonify({'error': 'Failed to parse evaluation. Please try again.'}), 500
        except json.JSONDecodeError as e:
            print(f"JSON Parse Error: {e}")
            print(f"Response: {response}")
            return jsonify({'error': 'Failed to parse evaluation'}), 500
    
    return jsonify({'error': 'Failed to evaluate response'}), 500

@app.route('/api/get-results')
def get_results():
    """Get stored results from session"""
    if 'evaluation' not in session:
        return jsonify({'error': 'No evaluation found'}), 404
    
    return jsonify({
        'success': True,
        'job_role': session.get('job_role'),
        'scenario': session.get('scenario'),
        'evaluation': session.get('evaluation')
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)