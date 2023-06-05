FROM ghcr.io/home-assistant/home-assistant:2023.4.4

# HA
EXPOSE 8123:8123/tcp

# HA Python debugging
EXPOSE 5678:5678/tcp

# Art-Net

COPY staging/.storage /config/.storage

COPY staging/configuration.yaml /config/configuration.yaml

COPY custom_components /config/custom_components