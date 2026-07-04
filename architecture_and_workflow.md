# System Architecture and Workflow

Here are the visual representations of how the Research Paper Guide system is structured and how data flows through it during a user's session.

## System Architecture

This diagram shows the major components: the React Frontend, the FastAPI Backend, the AI and Integration modules, and external services.

```mermaid
graph TD
    subgraph Frontend [Frontend - React + Vite]
        UI[User Interface & Dashboard]
        State[React State & Context]
    end

    subgraph Backend [Backend API - FastAPI]
        Router[API Routes / main.py]
        Auth[Authentication Module]
        
        subgraph AI_Engine [AI Engine Modules]
            TD_AI[Topic Discovery]
            GA_AI[Gap Analysis]
            MG_AI[Manuscript Generation]
            VR_AI[Venue Recommendation]
            GR_AI[Guardrails & Citation]
        end
        
        subgraph Integrations [Knowledge Integrations]
            ArXiv[arXiv API]
            Crossref[Crossref API]
            GitHub[GitHub Repos]
            OpenAlex[OpenAlex & Others]
        end
    end

    subgraph Database [Database]
        MongoDB[(MongoDB)]
    end
    
    subgraph External_LLM [External AI Services]
        OpenRouter[OpenRouter / LLMs]
    end

    UI <-->|REST API Calls| Router
    Router --> Auth
    Auth <-->|Verify / Store Users| MongoDB
    
    Router <-->|Delegates Tasks| AI_Engine
    Router <-->|Delegates Searches| Integrations
    
    AI_Engine <-->|Prompts & Generation| OpenRouter
    AI_Engine -.->|Reads Context| Integrations
    
    Integrations -.->|HTTP Requests| ArXiv
    Integrations -.->|HTTP Requests| Crossref
    Integrations -.->|HTTP Requests| GitHub
    Integrations -.->|HTTP Requests| OpenAlex
    
    Router <-->|Save / Load Drafts & Surveys| MongoDB
```

## End-to-End User Workflow

This sequence diagram illustrates the step-by-step journey of a researcher using the platform, from finding a topic to formatting their final manuscript.

```mermaid
sequenceDiagram
    actor User
    participant Front as Frontend (React)
    participant API as Backend (FastAPI)
    participant DB as MongoDB
    participant Sources as External Sources (arXiv, Crossref)
    participant LLM as OpenRouter AI

    User->>Front: 1. Input interest / area
    Front->>API: GET /api/topics
    API->>LLM: Analyze trends & suggest topics
    LLM-->>API: Topic recommendations
    API-->>Front: Display topics
    
    User->>Front: 2. Search Literature for Topic
    Front->>API: GET /api/literature
    API->>Sources: Query databases (arXiv, OpenAlex)
    Sources-->>API: Paper metadata & abstracts
    API-->>Front: Display papers
    User->>Front: Save relevant papers
    Front->>API: POST /api/literature/save
    API->>DB: Store saved survey
    
    User->>Front: 3. Start Drafting Section (e.g. Abstract)
    Front->>API: POST /api/manuscript
    API->>DB: Retrieve saved literature
    DB-->>API: Context
    API->>LLM: Generate section with guardrails & citations
    LLM-->>API: Draft content
    API-->>Front: Display draft
    User->>Front: Edit & Save manuscript
    Front->>API: POST /api/manuscript/save
    API->>DB: Store manuscript draft
    
    User->>Front: 4. Find Publication Venue
    Front->>API: POST /api/venues (send abstract)
    API->>LLM: Recommend matching journals
    LLM-->>API: List of venues
    API-->>Front: Display venue recommendations
    
    User->>Front: 5. Check Guidelines
    Front->>API: POST /api/guidelines
    API->>LLM: Align draft with venue format rules
    LLM-->>API: Formatting feedback & checklist
    API-->>Front: Display alignment checklist
```
