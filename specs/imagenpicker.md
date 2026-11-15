Architecture Overview

1. Feature Extraction (x): When a user submits a prompt, convert it into a fixed-length vector x (e.g., a 64- or 128-dimensional embedding, plus a bias term 1.0 at index 0). Keeping d around 32–128 ensures matrix operations stay tiny and fast.
2. Database State (Postgres): Store one row per image generator containing its posterior mean vector μ (length d) and precision matrix Λ = Σ⁻¹ (shape d x d). Storing precision Λ instead of covariance Σ avoids matrix inversion during updates.
3. Selection (Thompson Sampling): Load all 12 rows, sample a weight vector from each generator's multivariate normal posterior, compute xᵀw̃, and return the winning generator ID.
4. Feedback Loop: When the client posts the 1–5 rating back to FastAPI, run a rank-1 matrix update on that specific generator's row in Postgres.

1. Postgres Schema

Store the 12 generator states in a single lightweight table. Using DOUBLE PRECISION[] or JSONB works out of the box without extra extensions.
sql

```
CREATE TABLE generator_bandit_state (
    generator_id VARCHAR(64) PRIMARY KEY,
    -- Precision matrix (Lambda = Sigma^-1), stored as a 2D JSON array
    precision_matrix JSONB NOT NULL,
    -- Precision-weighted mean (b = Lambda * mu), stored as a 1D JSON array
    b_vector JSONB NOT NULL,
    observation_count INT DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

(Tip: Storing precision_matrix Λ and b_vector b = Λμ makes the database update purely additive—no matrix inversions needed when a user submits a score!)

2. Core Bayesian Bandit Logic (bandit.py)

This module handles initialization, Thompson Sampling selection, and exact Bayesian linear regression updates.
python
import numpy as np
from typing import List, Dict, Tuple

class ContextualGeneratorBandit:
    def __init__(self, dim: int, alpha_prior: float = 1.0, noise_precision: float = 1.0):
        """
        dim: Dimension of the prompt feature vector (including bias term).
        alpha_prior: Prior precision (higher = stronger pull toward neutral 3.0 rating).
        noise_precision: Expected consistency of user ratings (1 / variance).
        """
        self.dim = dim
        self.alpha_prior = alpha_prior
        self.beta = noise_precision

    def create_initial_state(self, default_score: float = 3.0) -> Tuple[List[List[float]], List[float]]:
        """Creates prior state for a new generator, centered at default_score."""
        precision = np.eye(self.dim) * self.alpha_prior
        mu_0 = np.zeros(self.dim)
        mu_0[0] = default_score  # Index 0 is the bias/intercept term
        b = precision @ mu_0
        return precision.tolist(), b.tolist()

    def select_generator(self, states: Dict[str, Tuple[np.ndarray, np.ndarray]], x: np.ndarray) -> str:
        """
        states: {generator_id: (precision_matrix, b_vector)}
        x: 1D numpy array of shape (dim,) with x[0] = 1.0
        """
        best_gen = None
        highest_sampled_score = -np.inf

        for gen_id, (precision, b) in states.items():
            # Solve Lambda * mu = b for the current posterior mean
            cov = np.linalg.inv(precision)
            mu = cov @ b
            
            # Thompson Sampling: draw a plausible weight vector from the posterior
            sampled_weights = np.random.multivariate_normal(mu, cov)
            sampled_score = float(x @ sampled_weights)

            if sampled_score > highest_sampled_score:
                highest_sampled_score = sampled_score
                best_gen = gen_id

        return best_gen

    def compute_update(
        self, 
        precision: np.ndarray, 
        b: np.ndarray, 
        x: np.ndarray, 
        rating: float,
        decay: float = 0.999
    ) -> Tuple[List[List[float]], List[float]]:
        """
        Updates the posterior with a new 1-5 rating.
        decay: Slight forgetting factor (< 1.0) so the model adapts if generators get updated.
        """
        # Apply slight forgetting factor to adapt to model drift over time
        new_precision = (precision * decay) + self.beta * np.outer(x, x)
        new_b = (b * decay) + self.beta * rating * x
        return new_precision.tolist(), new_b.tolist()
Use code with caution.

3. FastAPI Endpoints (main.py)

Wire the bandit into two endpoints: one to pick a generator before image creation, and one to ingest the client's rating afterward.
python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import numpy as np

app = FastAPI()
FEATURE_DIM = 33  # e.g., 1 bias term + 32 prompt features
bandit = ContextualGeneratorBandit(dim=FEATURE_DIM)

class PredictRequest(BaseModel):
    prompt: str

class FeedbackRequest(BaseModel):
    generator_id: str
    prompt: str
    rating: int = Field(..., ge=1, le=5)

def extract_features(prompt: str) -> np.ndarray:
    """
    Replace with your embedding model (e.g., PCA-reduced OpenAI/sentence-transformers)
    or keyword/style flags. Always keep index 0 as 1.0 for the baseline rating.
    """
    vec = np.zeros(FEATURE_DIM)
    vec[0] = 1.0  # Intercept
    # Example placeholder: hash/embedding features go into vec[1:]
    return vec

@app.post("/select-generator")
async def select_generator(req: PredictRequest):
    x = extract_features(req.prompt)
    # 1. Fetch all 12 rows from Postgres `generator_bandit_state`
    # rows = await db.fetch_all("SELECT generator_id, precision_matrix, b_vector FROM generator_bandit_state")
    states = {} # Populate {row.generator_id: (np.array(row.precision_matrix), np.array(row.b_vector))}
    
    chosen_id = bandit.select_generator(states, x)
    return {"generator_id": chosen_id}

@app.post("/feedback")
async def record_feedback(req: FeedbackRequest):
    x = extract_features(req.prompt)
    # 1. Fetch row for req.generator_id with `FOR UPDATE` to prevent race conditions
    # row = await db.fetch_one("SELECT precision_matrix, b_vector FROM generator_bandit_state WHERE generator_id = :id FOR UPDATE", ...)
    
    # Example placeholders for fetched state:
    precision = np.eye(FEATURE_DIM)
    b = np.zeros(FEATURE_DIM)
    
    new_precision, new_b = bandit.compute_update(precision, b, x, float(req.rating))
    
    # 2. Save `new_precision` and `new_b` back to Postgres and increment `observation_count`
    return {"status": "updated", "generator_id": req.generator_id}
Use code with caution.

Production Guardrails to Include
```

* Concurrency Locks (FOR UPDATE): If two users submit feedback for the same generator at the exact same millisecond, a standard read-then-write will overwrite one update. Always lock the single generator row inside a transaction during /feedback.
* Keep FEATURE_DIM Small: Because np.linalg.inv runs 12 times per /select-generator call, keep dim <= 128. At dim = 64, 12 inversions take under 0.5ms total. If you use a 1536-dim embedding model (like OpenAI text-embedding-3-small), project it down to 32–64 dimensions using a fixed random projection matrix or PCA first.
* Log Raw Events Separately: Keep an append-only feedback_logs table (user_id, prompt, generator_id, rating, created_at) alongside generator_bandit_state. If you ever change your feature extractor extract_features(), you can replay your historical logs to rebuild the bandit's state from scratch.
How do you plan to convert the user's goal/prompt into the feature vector x (e.g., an existing text embedding API, a local sentence-transformer model, or structured tags/dropdowns)? I can help you set up the dimensionality reduction or feature encoding step so it plugs directly into extract_features().

