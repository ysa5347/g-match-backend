# Kubernetes (k3s) 배포 가이드

## 개요

Raspberry Pi k3s 환경에서 G-Match Backend를 배포하는 가이드입니다.

---

## 전제 조건

### 1. k3s 설치 (Raspberry Pi)

```bash
# k3s 설치 (마스터 노드)
curl -sfL https://get.k3s.io | sh -

# 설치 확인
sudo k3s kubectl get nodes

# kubectl 별칭 설정
echo "alias kubectl='sudo k3s kubectl'" >> ~/.bashrc
source ~/.bashrc
```

### 2. Docker 이미지 빌드

```bash
# 1. 프로덕션 이미지 빌드 (tests/ 제외)
docker build -t g-match-backend:latest .

# 2. 이미지 크기 확인
docker images g-match-backend

# 3. 로컬 레지스트리 또는 Docker Hub에 푸시
# Option A: Docker Hub
docker tag g-match-backend:latest YOUR_USERNAME/g-match-backend:latest
docker push YOUR_USERNAME/g-match-backend:latest

# Option B: 로컬 레지스트리 (k3s 내부)
# k3s는 containerd를 사용하므로 이미지 import
sudo k3s ctr images import g-match-backend.tar
```

---

## 파일 구조

```
k8s/
├── namespace.yaml         # Namespace 생성
├── configmap.yaml         # 환경변수 (non-sensitive)
├── secret.yaml            # 환경변수 (sensitive)
├── mysql.yaml             # MySQL StatefulSet + Service
├── redis.yaml             # Redis Deployment + Service
├── django.yaml            # Django Deployment + Service + Ingress
└── kustomization.yaml     # Kustomize 설정
```

---

## 배포 단계

### 1. Secret 설정

**중요**: `k8s/secret.yaml` 파일의 비밀값을 실제 값으로 교체하세요!

```bash
# Django Secret Key 생성
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'

# secret.yaml 편집
nano k8s/secret.yaml

# 교체할 값:
# - SECRET_KEY
# - DB_PASSWORD
# - DB_ROOT_PASSWORD
# - EMAIL_HOST_USER
# - EMAIL_HOST_PASSWORD
```

### 2. 이미지 레지스트리 설정

`k8s/django.yaml`에서 이미지 경로 수정:

```yaml
# Before:
image: YOUR_REGISTRY/g-match-backend:latest

# After (예시):
image: yourusername/g-match-backend:latest
# 또는 로컬:
image: g-match-backend:latest
```

### 3. Ingress 도메인 설정

`k8s/django.yaml`에서 도메인 수정:

```yaml
spec:
  rules:
  - host: api.g-match.local  # 실제 도메인으로 변경
```

### 4. 배포 실행

```bash
# 1. Namespace 생성
kubectl apply -f k8s/namespace.yaml

# 2. ConfigMap 및 Secret 적용
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml

# 3. MySQL 배포
kubectl apply -f k8s/mysql.yaml

# 4. Redis 배포
kubectl apply -f k8s/redis.yaml

# 5. MySQL과 Redis가 준비될 때까지 대기
kubectl wait --for=condition=ready pod -l app=mysql -n g-match --timeout=300s
kubectl wait --for=condition=ready pod -l app=redis -n g-match --timeout=300s

# 6. Django 배포
kubectl apply -f k8s/django.yaml

# 7. 모든 리소스 확인
kubectl get all -n g-match
```

**또는 Kustomize 사용**:

```bash
# 한 번에 배포
kubectl apply -k k8s/

# 삭제
kubectl delete -k k8s/
```

---

## 배포 확인

### Pod 상태 확인

```bash
# 모든 Pod 확인
kubectl get pods -n g-match

# 특정 Pod 로그 확인
kubectl logs -n g-match deployment/g-match-web -f

# Pod 상세 정보
kubectl describe pod -n g-match <pod-name>
```

### Service 확인

```bash
# 서비스 목록
kubectl get svc -n g-match

# 서비스 상세
kubectl describe svc -n g-match g-match-web
```

### Ingress 확인

```bash
# Ingress 확인
kubectl get ingress -n g-match

# Ingress 상세
kubectl describe ingress -n g-match g-match-ingress

# Traefik 대시보드 확인 (k3s 기본)
kubectl -n kube-system get svc traefik
```

---

## 데이터베이스 초기화

### 1. 마이그레이션

```bash
# Django 마이그레이션 실행 (initContainer에서 자동 실행됨)
# 수동 실행:
kubectl exec -n g-match deployment/g-match-web -- python manage.py migrate
```

### 2. 슈퍼유저 생성

```bash
kubectl exec -it -n g-match deployment/g-match-web -- python manage.py createsuperuser
```

### 3. 정적 파일 수집

```bash
# initContainer에서 자동 실행됨
# 수동 실행:
kubectl exec -n g-match deployment/g-match-web -- python manage.py collectstatic --noinput
```

---

## 스케일링

### 수평 확장 (Horizontal Scaling)

```bash
# Django Pod 개수 조정
kubectl scale deployment/g-match-web -n g-match --replicas=3

# 자동 스케일링 (HPA)
kubectl autoscale deployment g-match-web -n g-match \
  --cpu-percent=70 \
  --min=2 \
  --max=5

# HPA 상태 확인
kubectl get hpa -n g-match
```

### 리소스 제한 조정

`k8s/django.yaml` 수정:

```yaml
resources:
  requests:
    memory: "512Mi"  # 최소 요구량
    cpu: "500m"
  limits:
    memory: "1Gi"    # 최대 사용량
    cpu: "1000m"
```

---

## 업데이트 및 롤백

### 이미지 업데이트

```bash
# 1. 새 이미지 빌드
docker build -t g-match-backend:v1.1.0 .
docker tag g-match-backend:v1.1.0 yourusername/g-match-backend:v1.1.0
docker push yourusername/g-match-backend:v1.1.0

# 2. Deployment 이미지 업데이트
kubectl set image deployment/g-match-web \
  django=yourusername/g-match-backend:v1.1.0 \
  -n g-match

# 3. 롤아웃 상태 확인
kubectl rollout status deployment/g-match-web -n g-match

# 4. 롤아웃 기록 확인
kubectl rollout history deployment/g-match-web -n g-match
```

### 롤백

```bash
# 이전 버전으로 롤백
kubectl rollout undo deployment/g-match-web -n g-match

# 특정 리비전으로 롤백
kubectl rollout undo deployment/g-match-web -n g-match --to-revision=2
```

---

## 볼륨 관리

### PersistentVolume 확인

```bash
# PVC 목록
kubectl get pvc -n g-match

# PV 목록
kubectl get pv

# 상세 정보
kubectl describe pvc -n g-match mysql-pvc
kubectl describe pvc -n g-match redis-pvc
```

### 백업

```bash
# MySQL 백업
kubectl exec -n g-match deployment/g-match-mysql -- \
  mysqldump -u root -p$MYSQL_ROOT_PASSWORD g_match > backup_$(date +%Y%m%d).sql

# Redis 백업
kubectl exec -n g-match deployment/g-match-redis -- \
  redis-cli BGSAVE
```

---

## 모니터링

### 리소스 사용량

```bash
# Node 리소스 사용량
kubectl top nodes

# Pod 리소스 사용량
kubectl top pods -n g-match

# 특정 Pod CPU/Memory
kubectl top pod -n g-match <pod-name>
```

### 로그 수집

```bash
# 실시간 로그
kubectl logs -n g-match deployment/g-match-web -f

# 최근 100줄
kubectl logs -n g-match deployment/g-match-web --tail=100

# 모든 Pod 로그
kubectl logs -n g-match -l app=g-match --all-containers=true
```

---

## 트러블슈팅

### 1. Pod가 시작되지 않음

```bash
# Pod 상태 확인
kubectl get pods -n g-match

# Pod 이벤트 확인
kubectl describe pod -n g-match <pod-name>

# 로그 확인
kubectl logs -n g-match <pod-name>

# 이전 컨테이너 로그 (Crash 시)
kubectl logs -n g-match <pod-name> --previous
```

### 2. 이미지 Pull 실패

```bash
# ImagePullBackOff 해결

# Option 1: 이미지를 k3s에 직접 import
docker save g-match-backend:latest > g-match-backend.tar
sudo k3s ctr images import g-match-backend.tar

# Option 2: imagePullPolicy 변경
kubectl patch deployment g-match-web -n g-match \
  -p '{"spec":{"template":{"spec":{"containers":[{"name":"django","imagePullPolicy":"IfNotPresent"}]}}}}'
```

### 3. 데이터베이스 연결 실패

```bash
# MySQL Pod 확인
kubectl get pods -n g-match -l app=mysql

# MySQL 로그 확인
kubectl logs -n g-match deployment/g-match-mysql

# MySQL 연결 테스트
kubectl exec -n g-match deployment/g-match-mysql -- \
  mysql -u django-server -p$DB_PASSWORD -e "SELECT 1"

# Django에서 DB 연결 테스트
kubectl exec -n g-match deployment/g-match-web -- \
  python manage.py dbshell
```

### 4. Ingress 접근 불가

```bash
# Ingress 상태 확인
kubectl get ingress -n g-match

# Traefik 로그 확인
kubectl logs -n kube-system deployment/traefik -f

# 서비스 엔드포인트 확인
kubectl get endpoints -n g-match g-match-web

# hosts 파일 설정 (로컬 테스트)
# Raspberry Pi IP: 192.168.1.100
echo "192.168.1.100 api.g-match.local" | sudo tee -a /etc/hosts
```

---

## Raspberry Pi 최적화

### 1. 리소스 제한 설정

Raspberry Pi의 제한된 리소스를 고려:

```yaml
# k8s/django.yaml
resources:
  requests:
    memory: "128Mi"  # Raspberry Pi용으로 낮춤
    cpu: "100m"
  limits:
    memory: "256Mi"
    cpu: "250m"
```

### 2. Replica 수 조정

```yaml
# k8s/django.yaml
spec:
  replicas: 1  # Raspberry Pi에서는 1개로 시작
```

### 3. 로컬 스토리지 사용

k3s의 기본 `local-path` StorageClass 사용 (이미 설정됨):

```yaml
# k8s/mysql.yaml, redis.yaml
spec:
  storageClassName: local-path
```

### 4. 메모리 스왑 비활성화

```bash
sudo swapoff -a
sudo sed -i '/ swap / s/^/#/' /etc/fstab
```

---

## 보안 강화

### 1. NetworkPolicy 적용

```yaml
# k8s/network-policy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: g-match-network-policy
  namespace: g-match
spec:
  podSelector:
    matchLabels:
      app: g-match
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: g-match
  egress:
  - to:
    - podSelector:
        matchLabels:
          app: mysql
  - to:
    - podSelector:
        matchLabels:
          app: redis
```

### 2. RBAC 설정

```yaml
# k8s/rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: g-match-sa
  namespace: g-match
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: g-match-role
  namespace: g-match
rules:
- apiGroups: [""]
  resources: ["pods", "services"]
  verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: g-match-rolebinding
  namespace: g-match
subjects:
- kind: ServiceAccount
  name: g-match-sa
roleRef:
  kind: Role
  name: g-match-role
  apiGroup: rbac.authorization.k8s.io
```

---

## CI/CD 통합

### GitHub Actions로 자동 배포

```yaml
# .github/workflows/k8s-deploy.yml
name: Deploy to k3s

on:
  push:
    tags:
      - 'v*'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2

      - name: Build Docker image
        run: |
          docker build -t ${{ secrets.DOCKER_USERNAME }}/g-match-backend:${{ github.ref_name }} .
          docker push ${{ secrets.DOCKER_USERNAME }}/g-match-backend:${{ github.ref_name }}

      - name: Deploy to k3s
        uses: appleboy/ssh-action@master
        with:
          host: ${{ secrets.K3S_HOST }}
          username: ${{ secrets.K3S_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          script: |
            kubectl set image deployment/g-match-web \
              django=${{ secrets.DOCKER_USERNAME }}/g-match-backend:${{ github.ref_name }} \
              -n g-match
            kubectl rollout status deployment/g-match-web -n g-match
```

---

## 요약

### ✅ tests/ 디렉토리 제외 확인

Docker 이미지 빌드 시 `.dockerignore`에 의해 **tests/ 디렉토리가 완전히 제외**됩니다:

```bash
# Dockerfile 빌드 시
docker build -t g-match-backend .

# .dockerignore에 포함:
tests/              ✅ 제외됨
CLAUDE/             ✅ 제외됨
.venv/              ✅ 제외됨
```

### 🚀 k3s 배포 플로우

1. **Docker 이미지 빌드** (tests/ 제외)
2. **이미지 레지스트리에 푸시** (또는 로컬 import)
3. **Secret 설정** (DB 비밀번호 등)
4. **kubectl apply** (순서대로)
5. **Pod 상태 확인**
6. **Ingress로 접속**

### 📊 Raspberry Pi k3s 권장 사양

- **최소**: Raspberry Pi 4 (4GB RAM)
- **권장**: Raspberry Pi 4 (8GB RAM) 또는 Raspberry Pi 5
- **스토리지**: SD Card 64GB 이상 (또는 SSD 권장)
- **네트워크**: 유선 연결 권장

---

**런타임에 tests/ 디렉토리는 완전히 불필요하며, 프로덕션 이미지에 포함되지 않습니다!**
