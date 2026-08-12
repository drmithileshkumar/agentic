@echo off
REM Shim so plasTeX can locate .tex files without a TeX Live installation.
REM See kpsewhich_shim.py for why this is needed.
python "%~dp0kpsewhich_shim.py" %*
