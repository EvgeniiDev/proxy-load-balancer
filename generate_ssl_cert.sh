#!/bin/bash

echo "Generating SSL certificates for HTTPS proxy..."

if [ -f "cert.pem" ] && [ -f "key.pem" ]; then
    echo "SSL certificates already exist. Skipping generation."
    echo "Delete cert.pem and key.pem to regenerate."
    exit 0
fi

openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:*.localhost,IP:127.0.0.1,IP:0.0.0.0"

if [ $? -eq 0 ]; then
    echo "SSL certificates generated successfully:"
    echo "  Certificate: cert.pem"
    echo "  Private key: key.pem"
    echo ""
    echo "Note: These are self-signed certificates for testing purposes only."
    echo "Browsers will show security warnings when accessing the HTTPS proxy."
    chmod 600 key.pem
    chmod 644 cert.pem
else
    echo "Error generating SSL certificates!"
    exit 1
fi
