# AttendEase

Face-recognition attendance system built with Django and DeepFace (SFace model). Employees check in and out by scanning their face; attendance is posted to the Perfect Office API (`https://office.digitalperfection.app/api`).

## Requirements

- Python 3.13 (see `runtime.txt`)
- A webcam
- Internet access (user sync, attendance posting, first-time model download)

## Setup

```bash
python -m venv venv
```

```bash
venv\Scripts\activate
```

On macOS/Linux use `source venv/bin/activate` instead.

```bash
pip install -r requirements.txt
```

```bash
python manage.py migrate
```

Pull the employee list from the Perfect Office API:

```bash
python manage.py sync_users
```

Create an admin login (optional, for `/admin/`):

```bash
python manage.py createsuperuser
```

## Run

```bash
python manage.py runserver 0.0.0.0:8000
```

Open http://127.0.0.1:8000. The first start takes a little longer while the face model loads in the background.

Browsers only allow camera access on `localhost` or HTTPS. To use the app from another device on the network, serve it over HTTPS, and add that origin to `CSRF_TRUSTED_ORIGINS` in `attendease/settings.py`.

## Usage

1. **Register a face** – go to `/register/select/`, pick the employee, and capture images from several angles in good lighting.
2. **Mark attendance** – on the home page, choose Check In or Check Out and scan your face.
3. **Admin** – `/admin/` shows users, face embeddings, and pending attendance syncs.

If the Perfect Office API is unreachable, attendance is queued locally and retried automatically by a background worker.

## Management commands

| Command | Purpose |
| --- | --- |
| `python manage.py sync_users` | Create/update/remove local users to match the API. Run when staff change. |
| `python manage.py sync_users --dry-run` | Preview the sync without saving. |
| `python manage.py compute_embeddings` | Build embeddings for face images that don't have one. |
| `python manage.py compute_embeddings --user <username> --force` | Recompute embeddings for one user. |
| `python manage.py update_api_ids --dry-run` | Preview re-matching users to API IDs by name (drop `--dry-run` to apply). |

Note: `sync_users` deletes local users that no longer exist in the API.

## Configuration

| Variable | Effect |
| --- | --- |
| `DATABASE_URL` | Use Postgres instead of the default `db.sqlite3`. |
| `SECRET_KEY` | Django secret key. Set this in production. |
| `RENDER` | When set, turns `DEBUG` off. |

The API base URL is set in `accounts/api_service.py`; the recognition threshold (`DISTANCE_THRESHOLD`) is in `accounts/simple_views.py`.

## Deployment

`build.sh` installs dependencies, collects static files, and runs migrations. The `Procfile` starts the app with:

```bash
gunicorn attendease.wsgi:application --bind 0.0.0.0:$PORT
```

## Where things live

- `accounts/simple_views.py` – pages and recognition endpoints
- `accounts/api_service.py` – Perfect Office API calls
- `accounts/retry_worker.py` – retries failed attendance posts
- `media/faces/<username>/` – registered face images
- `logs/face_recognition.log` – recognition and API logs (rotated daily)

## Troubleshooting

- **Camera blocked** – use `localhost` or HTTPS and allow camera permission in the browser.
- **Face not recognised** – improve lighting, or delete and re-register the face with more varied images.
- **`sync_users` fails** – check that the API URL is reachable; try `--dry-run` first.
- **Slow first scan** – the model is still warming up; wait for "SFace model ready" in the console.
