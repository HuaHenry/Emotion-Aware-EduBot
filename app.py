from flask import Flask, render_template, request, jsonify, Response
from openai import OpenAI
import time
import json
from datetime import datetime
from collections import deque

app = Flask(__name__)

# Initialize OpenAI client
client = OpenAI(
    base_url='https://api.openai-proxy.org/v1',
    api_key='YOUR_OPENAI_API_KEY',
)

# Negative emotion keywords to detect
# NEGATIVE_WORDS = [
#     "sad", "depressed", "angry", "frustrated", "anxious", 
#     "stressed", "overwhelmed", "hopeless", "lonely", 
#     "scared", "worried", "tired", "exhausted", "upset"
# ]

def load_negative_words(file_path='negative_words.txt'):
    try:
        print(f"Loading negative words from {file_path}...")
        with open(file_path, 'r', encoding='utf-8') as f:
            return [line.strip().lower() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Warning: {file_path} not found. Using empty NEGATIVE_WORDS list.")
        return []

NEGATIVE_WORDS = load_negative_words()

# Enhanced system prompt
SYSTEM_PROMPT = """
You are an empathetic AI tutor named EduBot. Your primary role is to assist students with their learning while being attentive to their emotional state.

Guidelines:
1. When you detect negative emotions (from words or behavior patterns), respond with empathy first before addressing the academic content.
2. If the student seems stressed/upset, gently ask if they'd like to talk about it or take a break.
3. Maintain a supportive, non-judgmental tone throughout.
4. For academic questions, provide clear, concise explanations while still being emotionally aware.
5. Keep responses concise (2-3 sentences) unless more detail is requested.
6. Maintain context across multiple conversation turns.

Emotional support examples:
- "I notice you might be feeling [emotion]. Would you like to share more about that?"
- "It's completely normal to feel this way sometimes. Would a short break help?"
- "I'm here to listen if you'd like to talk about what's bothering you."
"""

# Conversation history storage (in-memory for simplicity)
conversation_history = {}

def get_conversation_history(session_id):
    if session_id not in conversation_history:
        conversation_history[session_id] = deque(maxlen=20)  # Keep last 20 messages
        # Initialize with system prompt
        conversation_history[session_id].append({
            "role": "system",
            "content": SYSTEM_PROMPT
        })
    return conversation_history[session_id]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_session', methods=['POST'])
def start_session():
    session_id = str(time.time())  # Simple session ID generation
    get_conversation_history(session_id)  # Initialize history
    return jsonify({"session_id": session_id})

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data['message']
    session_id = data.get('session_id')
    behavior_data = data.get('behavior', {})
    
    if not session_id:
        return jsonify({"error": "Session ID required"}), 400
    
    # Get conversation history
    history = get_conversation_history(session_id)
    
    # Check for negative words in user message
    negative_words_detected = [word for word in NEGATIVE_WORDS if word in user_message.lower()]
    
    # Analyze behavior patterns for stress indicators
    is_stressed = analyze_behavior_patterns(behavior_data)
    
    # Add user message to history
    history.append({
        "role": "user",
        "content": user_message,
        "timestamp": datetime.now().isoformat()
    })
    
    # Prepare messages for API (last 6 messages for context)
    messages = list(history)[-6:]  # Send last 6 messages for context
    
    # Add context if negative emotions detected
    if negative_words_detected or is_stressed:
        emotion_context = "System Note: User appears to be experiencing "
        if negative_words_detected:
            emotion_context += f"negative emotions (detected words: {', '.join(negative_words_detected)}). "
        if is_stressed:
            emotion_context += "behavioral signs of stress (based on interaction patterns). "
        emotion_context += "Please respond with appropriate emotional support."
        
        # Insert after system prompt but before conversation history
        if len(messages) > 0 and messages[0]["role"] == "system":
            messages.insert(1, {
                "role": "system",
                "content": emotion_context
            })
        else:
            messages.insert(0, {
                "role": "system",
                "content": emotion_context
            })
    
    # Stream the response from OpenAI
    def generate():
        start_time = time.time()
        stream = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            stream=True,
            temperature=0.7
        )
        
        first_chunk = True
        bot_response_content = ""
        
        for chunk in stream:
            if not chunk.choices:  # Handle empty choices
                continue
                
            if chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                bot_response_content += content
                
                if first_chunk:
                    # Send inference time with first chunk
                    inference_time = round((time.time() - start_time) * 1000)
                    yield f"data: {json.dumps({'content': content, 'inference_time': inference_time})}\n\n"
                    first_chunk = False
                else:
                    yield f"data: {json.dumps({'content': content})}\n\n"
        
        # Add bot response to conversation history
        if bot_response_content:
            history.append({
                "role": "assistant",
                "content": bot_response_content,
                "timestamp": datetime.now().isoformat()
            })
    
    return Response(generate(), mimetype='text/event-stream')

def analyze_behavior_patterns(behavior_data):
    """Analyze user behavior patterns for stress indicators"""
    if not behavior_data:
        return False
    
    # Check typing speed fluctuation (> ±50%)
    typing_speed = behavior_data.get('typingSpeed', {})
    if typing_speed:
        avg_speed = typing_speed.get('average', 0)
        min_speed = typing_speed.get('min', avg_speed)
        max_speed = typing_speed.get('max', avg_speed)
        if avg_speed > 0:
            fluctuation = max((max_speed - min_speed) / avg_speed, (min_speed - max_speed) / avg_speed)
            if fluctuation > 0.5:
                return True
    
    # Check key interval standard deviation (>200ms)
    if behavior_data.get('keyIntervalStd', 0) > 200:
        return True
    
    # Check consecutive backspaces (≥3)
    if behavior_data.get('consecutiveBackspaces', 0) >= 3:
        return True
    
    # Check response interval (>45 seconds)
    if behavior_data.get('responseInterval', 0) > 45000:  # in milliseconds
        return True
    
    return False

if __name__ == '__main__':
    app.run(debug=True)