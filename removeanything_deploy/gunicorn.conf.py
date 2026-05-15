import os

bind = f"0.0.0.0:{os.environ.get('PORT', '7860')}"
workers = 1            # keep low — AI models are RAM-heavy
threads = 2
timeout = 120          # rembg can take 30-60s on cold start
graceful_timeout = 30
keepalive = 5
preload_app = False    # don't preload — saves RAM on boot
loglevel = "info"
accesslog = "-"
errorlog  = "-"
