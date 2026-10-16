"""Predict a *tentative* floor from a Wi-Fi fingerprint.

Examples:
    python predict.py --json '{"aa:bb:cc:dd:ee:ff": -65}'
    python predict.py --scan 'craxiom-wifi-20261014-231704.csv'

Use ONLY for research: the supplied model was trained with contradictory labels.
"""
import argparse
import json
from pathlib import Path
import joblib
import pandas as pd

DEFAULT_MODEL = Path(__file__).resolve().parent / 'model.pkl'
if not DEFAULT_MODEL.exists():
    DEFAULT_MODEL = Path(__file__).resolve().parent / 'outputs' / 'wifi_floor_experimental.pkl'


def read_latest_network_survey(path: str):
    raw = pd.read_csv(path, comment='#')
    for name in ('deviceTime', 'bssid', 'signalStrength'):
        if name not in raw.columns:
            raise ValueError(f'Required Network Survey column missing: {name}')
    raw = raw.dropna(subset=['deviceTime', 'bssid', 'signalStrength'])
    if raw.empty:
        raise ValueError('No valid Wi-Fi readings in CSV')
    # Network Survey rows in the same scan share deviceTime.
    latest = raw['deviceTime'].max()
    frame = raw[raw['deviceTime'] == latest]
    return frame.groupby('bssid')['signalStrength'].mean().to_dict(), str(latest)


def predict_signals(bundle: dict, signals: dict, min_known=3):
    cols = bundle['feature_names']
    readings = {}
    for key, value in signals.items():
        if key in cols:
            value = float(value)
            if not (-110 <= value <= 0):
                raise ValueError(f'RSSI out of range for {key}: {value}')
            readings[key] = value
    if len(readings) < min_known:
        raise ValueError(f'At least {min_known} recognized BSSIDs required; got {len(readings)}. Cannot localize.')
    vector = pd.DataFrame([{col: readings.get(col, -110.0) for col in cols}], columns=cols)
    model = bundle['model']
    label = str(model.predict(vector)[0])
    response = {
        'predicted_floor': label,
        'status': bundle['status'],
        'recognized_access_points': len(readings),
        'total_model_access_points': len(cols),
        'message': 'Experimental estimate only. Training floor labels conflict; do not use for navigation.',
    }
    if hasattr(model, 'predict_proba'):
        probs = model.predict_proba(vector)[0]
        response['uncalibrated_model_scores'] = {str(k): round(float(v), 4) for k, v in zip(model.classes_, probs)}
    return response


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--model', default=str(DEFAULT_MODEL))
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--json', help='JSON mapping BSSID -> RSSI')
    g.add_argument('--scan', help='Network Survey CSV file; uses last scan timestamp')
    args = p.parse_args()
    bundle = joblib.load(args.model)
    if args.scan:
        signals, timestamp = read_latest_network_survey(args.scan)
        print('Scan timestamp:', timestamp)
    else:
        signals = json.loads(args.json)
    print(json.dumps(predict_signals(bundle, signals), indent=2))


if __name__ == '__main__':
    main()
