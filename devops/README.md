# **Backend Deployment Script**


---

## **Setup Instructions**

### **Step 1: Copy the Deployment Script**
Navigate to the project root directory and copy the script:

```bash
cd dare-backend/devops
cp deploy.sh ~/deploy_be.sh
```

### **Step 2: Make the Script Executable**
Change the file permissions to allow execution:

```bash
chmod +x ~/deploy_be.sh
```

### **Step 3: Execute the Deployment Script**
Run the script to deploy the backend:

```bash
~/deploy_be.sh
```

---

## **What This Script Does**
1. **Navigates to the backend project directory** (`/home/deployer/dare-backend`).
2. **Pulls the latest changes** from the `main` branch.
3. **Activates the virtual environment** (`.venv`).
4. **Installs dependencies** from `requirements/prod.txt`.
5. **Applies any new database migrations** using `python manage.py migrate`.
6. **Collects static files** using `python manage.py collectstatic --noinput`.
7. **Restarts the backend service** (`dare`) and Nginx.

---

## **Troubleshooting**
- **Permission Denied?**  
  Ensure the script has execution permissions:  
  ```bash
  chmod +x ~/deploy_be.sh
  ```
- **Virtual Environment Not Found?**  
  Ensure the `.venv` folder exists in `/home/deployer/dare-backend/` and contains the necessary Python dependencies.
- **Deployment Not Reflecting Changes?**  
  - Check if `git pull` is fetching the latest changes.
  - Ensure the services are restarting properly with:  
    ```bash
    sudo systemctl status dare
    sudo systemctl status nginx
    ```
- **Database Issues?**  
  If `python manage.py migrate` fails, check the database connection and logs.

---
