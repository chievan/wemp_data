# Skill: General Server Management (1Panel Terminal)

This skill describes how to access the remote server's terminal via the 1Panel dashboard to perform various administrative or deployment tasks.

## 1. Access Credentials & Entry
- **Panel URL**: `http://106.54.219.69:27284/ipanel`
- **Username**: `chievan`
- **Password**: `shcjdx2018`
- **Terminal URL**: `http://106.54.219.69:27284/hosts/terminal`

## 2. Standard Operation Procedure (SOP)

### Phase 1: Authentication
1. Navigate to the **Panel URL**.
2. Log in using the credentials above.

### Phase 2: Terminal Engagement
1. Navigate to the **Terminal URL**.
2. Wait for the terminal (xterm/canvas) to initialize and show the prompt.
3. Ensure the input focus is set on the terminal area.

### Phase 3: Task Execution
Input the required commands based on the specific need. 

#### Example: Frontend Deployment
```bash
cd /opt/wemp_data/frontend
npm run build
pm2 restart wemp-frontend
```

#### Example: Backend Logs Check
```bash
pm2 logs bond-backend --lines 100
```

#### Example: System Health
```bash
top -n 1
df -h
```

## 3. Security & Maintenance
- **Secure Entry**: If the login fails with a "Security Entry" error, retrieve the updated URL by running `1pctl user-info` via SSH (if available).
- **Environment**: All operations are performed within the `/opt/wemp_data` or root environments as needed.

---
*Note: Use this SOP whenever a task requires direct server interaction via the 1Panel web terminal.*

