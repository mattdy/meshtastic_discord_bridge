FROM python:3.9 

ADD requirements.txt .
ADD meshtastic_discord_bridge.py .

RUN pip install -r requirements.txt

CMD ["python", "meshtastic_discord_bridge.py"]
