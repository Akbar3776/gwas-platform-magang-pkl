# GEMMA (Genome-wide Efficient Mixed Model Association)

Repository: [https://github.com/genetics-statistics/GEMMA](https://github.com/genetics-statistics/GEMMA)

Folder ini berisi binary dan wrapper eksekusi GEMMA v0.98.5 untuk platform GWAS:

## File

- `gemma`: Binary resmi Linux statis (v0.98.5 x86_64) dari rilis GitHub. Digunakan di Linux/Docker container dan WSL2.
- `gemma_wrapper.py`: Script wrapper Python lintas-platform yang mendeteksi dan mentranslasikan path Windows (`D:\...`) ke format WSL (`/mnt/d/...`) secara transparan.
- `gemma.cmd` / `gemma.bat`: Wrapper Windows yang dapat langsung dipanggil dari Command Prompt, PowerShell, atau Celery worker subprocess di Windows.

## Cara Penggunaan

### Di Windows (PowerShell / Command Prompt):
```powershell
backend\gemma\gemma.cmd -h 1
```

### Di Linux / Docker / WSL:
```bash
./backend/gemma/gemma -h 1
```

### Konfigurasi `.env`:
```env
GEMMA_BINARY_PATH=D:\Games\Code\GWAS_Study_ML\gwas-platform\backend\gemma\gemma.cmd
```
*(Atau `gemma` jika berjalan di dalam container Docker).*
