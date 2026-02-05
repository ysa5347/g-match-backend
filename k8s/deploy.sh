#!/bin/bash
# =============================================================================
# G-Match CD Deploy Script (lightweight, no external tools needed)
#
# Usage:
#   ./deploy.sh                    # Deploy all (infra + django + matcher)
#   ./deploy.sh django             # Deploy django only (migrate + rollout)
#   ./deploy.sh matcher            # Deploy matcher only
#   ./deploy.sh infra              # Deploy mysql + redis only
#   ./deploy.sh mysql              # Deploy mysql only
#   ./deploy.sh redis              # Deploy redis only
#   ./deploy.sh migrate            # Run migration only
#   ./deploy.sh status             # Show deployment status
#   ./deploy.sh django sha-abc123  # Deploy django with specific tag
#   ./deploy.sh TAG                # Deploy all with specific image tag
# =============================================================================

set -euo pipefail

NAMESPACE="g-match"
REGISTRY="ghcr.io/ysa5347"
DJANGO_IMAGE="${REGISTRY}/g-match-backend"
MATCHER_IMAGE="${REGISTRY}/g-match-backend-matcher"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[DEPLOY]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ---------------------------------------------------
# Functions
# ---------------------------------------------------

ensure_namespace() {
    if ! kubectl get namespace "$NAMESPACE" &>/dev/null; then
        log "Creating namespace: ${NAMESPACE}"
        kubectl create namespace "$NAMESPACE"
    fi
}

show_status() {
    log "=== Deployment Status ==="
    echo ""
    kubectl get pods -n "$NAMESPACE" -o wide
    echo ""
    kubectl get deployments -n "$NAMESPACE"
    echo ""
    kubectl get statefulsets -n "$NAMESPACE" 2>/dev/null || true
    echo ""
    kubectl get jobs -n "$NAMESPACE" 2>/dev/null || true
    echo ""
    kubectl get pvc -n "$NAMESPACE" 2>/dev/null || true
}

deploy_mysql() {
    log "Deploying MySQL..."
    kubectl apply -f "${SCRIPT_DIR}/mysql.yaml"

    log "Waiting for MySQL to be ready..."
    kubectl rollout status "statefulset/g-match-mysql" -n "$NAMESPACE" --timeout=300s
    log "MySQL deployed successfully"
}

deploy_redis() {
    log "Deploying Redis..."
    kubectl apply -f "${SCRIPT_DIR}/redis.yaml"

    log "Waiting for Redis to be ready..."
    kubectl rollout status "deployment/g-match-redis" -n "$NAMESPACE" --timeout=120s
    log "Redis deployed successfully"
}

deploy_infra() {
    deploy_mysql
    deploy_redis
}

run_migrate() {
    local tag="${1:-latest}"
    log "Running migration with image tag: ${tag}"

    # Delete old job if exists
    kubectl delete job g-match-migrate -n "$NAMESPACE" --ignore-not-found

    # Apply migration job with image tag override
    kubectl apply -f "${SCRIPT_DIR}/migration-job.yaml"

    if [ "$tag" != "latest" ]; then
        kubectl set image "job/g-match-migrate" \
            "migrate=${DJANGO_IMAGE}:${tag}" \
            -n "$NAMESPACE"
    fi

    log "Waiting for migration to complete..."
    if kubectl wait --for=condition=complete "job/g-match-migrate" \
        -n "$NAMESPACE" --timeout=180s; then
        log "Migration completed successfully"
        kubectl logs "job/g-match-migrate" -n "$NAMESPACE" --tail=20
    else
        err "Migration failed or timed out"
        kubectl logs "job/g-match-migrate" -n "$NAMESPACE" --tail=50
        exit 1
    fi
}

deploy_django() {
    local tag="${1:-latest}"
    log "Deploying Django with tag: ${tag}"

    kubectl set image "deployment/g-match-web" \
        "django=${DJANGO_IMAGE}:${tag}" \
        "django-collectstatic=${DJANGO_IMAGE}:${tag}" \
        -n "$NAMESPACE"

    log "Waiting for rollout..."
    kubectl rollout status "deployment/g-match-web" -n "$NAMESPACE" --timeout=180s
    log "Django deployed successfully"
}

deploy_matcher() {
    local tag="${1:-latest}"
    log "Deploying Matcher with tag: ${tag}"

    kubectl set image "deployment/g-match-edge-calculator" \
        "edge-calculator=${MATCHER_IMAGE}:${tag}" \
        -n "$NAMESPACE"

    kubectl set image "deployment/g-match-scheduler" \
        "scheduler=${MATCHER_IMAGE}:${tag}" \
        -n "$NAMESPACE"

    log "Waiting for rollout..."
    kubectl rollout status "deployment/g-match-edge-calculator" -n "$NAMESPACE" --timeout=120s
    kubectl rollout status "deployment/g-match-scheduler" -n "$NAMESPACE" --timeout=120s
    log "Matcher deployed successfully"
}

# ---------------------------------------------------
# Main
# ---------------------------------------------------

TARGET="${1:-all}"
TAG="${2:-latest}"

case "$TARGET" in
    status)
        show_status
        ;;
    migrate)
        run_migrate "$TAG"
        ;;
    mysql)
        ensure_namespace
        deploy_mysql
        show_status
        ;;
    redis)
        ensure_namespace
        deploy_redis
        show_status
        ;;
    infra)
        ensure_namespace
        deploy_infra
        show_status
        ;;
    django)
        run_migrate "$TAG"
        deploy_django "$TAG"
        show_status
        ;;
    matcher)
        deploy_matcher "$TAG"
        show_status
        ;;
    all)
        ensure_namespace
        deploy_infra
        run_migrate "$TAG"
        deploy_django "$TAG"
        deploy_matcher "$TAG"
        show_status
        ;;
    *)
        # Treat unknown first arg as a tag for full deploy
        TAG="$TARGET"
        ensure_namespace
        deploy_infra
        run_migrate "$TAG"
        deploy_django "$TAG"
        deploy_matcher "$TAG"
        show_status
        ;;
esac

log "Done!"
