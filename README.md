# Research Agent: AI Publishing Platform

An AI-powered application designed to streamline the academic research and publishing process. This platform helps researchers discover topics, conduct literature surveys, draft manuscripts, and find the perfect publication venue—all in one place.

**🔴 Live Site:** [https://ai-powered-research-agent-live.onrender.com](https://ai-powered-research-agent-live.onrender.com)
## 🚀 Features

*   **Dashboard & Topic Discovery:** Find trending research areas by leveraging OpenRouter AI and GitHub data.
*   **Literature Survey:** Automatically pull and organize relevant papers from arXiv and Crossref.
*   **Manuscript Builder:** Draft your paper section-by-section with AI assistance. Includes a sleek, auto-saving interface.
*   **Venue Recommendations:** Match your manuscript against journals and conferences to find the highest chance of acceptance based on formatting guidelines and scope.

## 🛠️ Tech Stack

*   **Frontend:** React, Vite, React Router, Tailwind-like custom CSS
*   **Backend:** FastAPI (Python), MongoDB, OpenRouter AI, integration with Crossref and arXiv APIs
*   **Animations:** Lottie JSON, custom CSS keyframes

## 💻 Running Locally

### 1. Backend Setup
1. Navigate to the `backend` folder:
   ```bash
   cd backend
   ```
2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file in the `backend` folder and add your API keys (like `OPENROUTER_API_KEY`, `MONGODB_URI`, `JWT_SECRET_KEY`).
4. Start the backend server:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

### 2. Frontend Setup
1. Open a new terminal and navigate to the `frontend` folder:
   ```bash
   cd frontend
   ```
2. Install the Node modules:
   ```bash
   npm install
   ```
3. Start the development server:
   ```bash
   npm run dev
   ```

## 🌐 Production Deployment

The project is configured for production environments:
*   Frontend API calls are dynamically routed via `VITE_API_URL`.
*   Backend CORS policies are securely managed via the `CORS_ORIGINS` environment variable. 

Enjoy your streamlined research process!
