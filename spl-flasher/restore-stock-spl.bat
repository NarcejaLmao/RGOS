@echo off
setlocal

set SCRIPT_DIR=%~dp0

"%SCRIPT_DIR%spd_dump.exe" --wait 300 keep_charge 1 ^
    fdl "%SCRIPT_DIR%fdl1-dl.bin" 0x5500 ^
    fdl "%SCRIPT_DIR%fdl2-dl.bin" 0x9EFFFE00 ^
    exec ^
    write_part splloader "%SCRIPT_DIR%spl_stock.img" ^
    write_part splloader_bak "%SCRIPT_DIR%spl_stock.img" ^
    poweroff

if errorlevel 1 (
    echo SPL flash failed!
    pause
    exit /b 1
)

echo SPL flash complete!
echo Your device has been restored to the stock SPL!
pause
