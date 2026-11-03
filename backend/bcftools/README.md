# BCFtools (samtools/bcftools)

Repository: [https://github.com/samtools/bcftools](https://github.com/samtools/bcftools)

BCFtools adalah rangkaian program utilitas untuk membaca, memvalidasi, menormalisasi, mengurutkan (`sort`), dan mengindeks varian genomik VCF/BCF (digunakan pada **Tahap 2 Pipeline Normalisasi VCF**).

---

## File dalam Direktori Ini

- `bcftools_wrapper.py`: Script wrapper Python lintas-platform yang menerjemahkan path file Windows (`D:\...`) ke path mount WSL (`/mnt/d/...`) secara otomatis.
- `bcftools.cmd` / `bcftools.bat`: File eksekusi Windows batch yang dapat langsung dipanggil dari PowerShell, Command Prompt, atau proses Python backend.

## Cara Penggunaan

### Di Windows (Command Prompt / PowerShell):
```powershell
backend\bcftools\bcftools.cmd --version
```

### Konfigurasi `.env`:
```env
BCFTOOLS_BINARY_PATH=D:/Games/Code/GWAS_Study_ML/gwas-platform/backend/bcftools/bcftools.cmd
```
