# Campus Cut Studio

Campus Cut Studio: private-access browser video editor for campus events, deployed via Railway.

Features: video upload and preview, selectable highlight clips, landscape/portrait/square MP4 export, optional SRT subtitle burn-in, optional HyperFrames title overlays.

## Production

Builds from Dockerfile. Set `APP_ACCESS_USER` and `APP_ACCESS_PASSWORD` (>=16 characters) as Railway environment variables; set `PUBLIC_MODE=1` and attach a Railway Volume at `/data` for uploaded files. Never commit credentials or student footage.

This repository is currently public; the deployed web app has separate access control. Change repository visibility to Private in GitHub Settings to keep its source private.
