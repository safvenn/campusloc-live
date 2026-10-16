"""Research-only floor prediction API; NOT suitable for real navigation."""
from contextlib import asynccontextmanager
from pathlib import Path
import joblib
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from predict import predict_signals

MODEL_PATH = Path(__file__).resolve().parent / 'outputs' / 'wifi_floor_experimental.pkl'


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.bundle = joblib.load(MODEL_PATH)  # Only load trusted local artifacts.
    yield
    app.state.bundle = None


app = FastAPI(title='CampusLoc Experimental Floor Classifier', lifespan=lifespan)


class Scan(BaseModel):
    signals: dict[str, float] = Field(min_length=3)


@app.get('/health')
def health(request: Request):
    b = request.app.state.bundle
    return {'status': 'ready', 'model_status': b['status'], 'selected_model': b['selected_model']}


@app.post('/predict')
def predict(scan: Scan, request: Request):
    try:
        return predict_signals(request.app.state.bundle, scan.signals)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
