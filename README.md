
# Manual-Based Online Quiz System

## What this version does
- Account creation and login
- Each account has its own uploaded study materials
- Upload PDF, DOCX, TXT, or MD study manuals from inside the running application
- Extracts text from the uploaded manual
- Generate Questions screen:
  - Study Material: required
  - Number of Questions: REQUIRED
  - Difficulty: REQUIRED (Easy/Medium/Hard)
  - Chapter: OPTIONAL
  - Topic: OPTIONAL
- Questions are generated from the uploaded manual, not from the app-development code
- Server-side answer checking and score calculation
- Dashboard with uploaded materials and quiz history

## Windows setup
Open Command Prompt in this folder:

    py -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    py app.py

Then open:

    http://127.0.0.1:5000

## Important
This starter uses a local, deterministic comprehension generator so it can run without an AI API key.
For production-quality AI-generated questions (especially better chapter/topic understanding and difficulty control), connect an AI model/API to `generate_questions()` while keeping the same rule: the model receives only the user's uploaded study material and the selected chapter/topic.

Do not put API keys into HTML or JavaScript. Use environment variables on the server.

Scanned/image-only PDFs need OCR before text can be extracted.
