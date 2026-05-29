# Deploy: production

1. Build image:
   `docker build -f infra/docker/Dockerfile -t ghcr.io/sanjays2402/clawhum:vX.Y .`
2. Push and `helm upgrade --install`.
3. Pre-warm cache: `kubectl exec ... -- python -c "from transformers import ClapModel; ClapModel.from_pretrained('laion/clap-htsat-unfused')"`.
4. Verify: `curl https://.../health`.
