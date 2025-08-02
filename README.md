# Interview Preparation and Assessment Tool

A Streamlit-based application that helps interviewers prepare for technical interviews by generating relevant questions, coding problems, and tracking candidate progress through the hiring pipeline.

## Features

- **Resume Parsing**: Upload and parse candidate resumes to extract key information
- **Automated Question Generation**: Generate relevant interview questions with model answers
- **Coding Problem Generator**: Create programming challenges with solutions in multiple languages
- **Interview Rounds**: Support for multiple interview levels (L1, L2, L3)
- **Candidate Tracking**: Track candidate status throughout the hiring process
- **Assessment Dashboard**: Record and review interview feedback
- **AWS S3 Integration**: Securely store resumes and assessment data

## Prerequisites

- Python 3.8+
- OpenAI API key
- AWS account with S3 access
- Required Python packages 

   ```
 **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
 
 **Set up environment variables**
   Create a `.env` file in the project root with the following variables:
   ```
   OPENAI_API_KEY=your_openai_api_key
   AWS_ACCESS_KEY_ID=your_aws_access_key
   AWS_SECRET_ACCESS_KEY=your_aws_secret_key
   AWS_REGION=your_aws_region
   S3_BUCKET_NAME=your_s3_bucket_name
   S3_BUCKET_FEEDBACK=your_s3_feedback_bucket
   GITHUB_API_TOKEN=your_github_token  # Optional
   ```

## Usage

1. **Run the application**
   ```bash
   streamlit run domain_qa.py
   ```

2. **Using the application**
   - Upload a candidate's resume (PDF)
   - The system will parse the resume and generate interview materials
   - Use the tabs to navigate between:
     - Candidate Brief
     - Technical Questions
     - Coding Problems
     - Interview Assessment
   - Track candidate status through the hiring pipeline
   - Save interview feedback and notes

## Workflow

1. **Resume Upload**
   - Upload a candidate's resume in PDF format
   - The system extracts key information using OpenAI's API

2. **Interview Preparation**
   - Generate domain-specific questions with model answers
   - Create coding problems with solutions
   - Get a quick candidate brief

3. **Interview Assessment**
   - Record interview notes and feedback
   - Rate candidate performance
   - Update candidate status in the hiring pipeline

4. **Status Tracking**
   - Monitor candidates through different interview stages
   - View historical status changes
   - Filter candidates by status

## File Structure

- `domain_qa.py`: Main application file
- `requirements.txt`: Python dependencies
- `.env`: Environment variables (not committed to version control)

## Dependencies

- streamlit
- PyPDF2
- openai
- boto3
- python-dotenv
- pandas

## Security Note

- Never commit your `.env` file or API keys to version control
- Ensure proper IAM roles and permissions for AWS services
- Use environment variables for sensitive information

## License

[Specify your license here]

## Support

For issues and feature requests, please open an issue in the repository.
