import sys
import traceback

def log(msg):
    print(f"[DEBUG]: {msg}")

try:
    log("1. Kiem tra thu vien co ban...")
    import os, time, base64, threading
    log("   -> OK!")

    log("2. Kiem tra thu vien flet...")
    import flet as ft
    log(f"   -> OK! Flet version: {getattr(ft, '__version__', 'unknown')}")

    log("3. Kiem tra import database...")
    import database
    log("   -> OK!")

    log("4. Kiem tra import DUANCLB...")
    import DUANCLB
    log("   -> OK!")

    log("5. Kiem tra khoi tao flet don gian...")
    def simple_main(page: ft.Page):
        log("   -> simple_main chay thanh cong!")
        page.title = "Test Flet Catch Error"
        page.add(ft.Text("Flet hoat dong binh thuong!"))

    log("   -> Bat dau ft.app(target=simple_main)...")
    ft.app(target=simple_main, port=8551, view=ft.AppView.WEB_BROWSER)

except Exception as e:
    print("\n" + "="*60)
    print("PHAT HIEN LOI TRONG QUA TRINH CHAY:")
    print("="*60)
    traceback.print_exc()
    print("="*60)
