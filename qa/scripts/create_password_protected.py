from reportlab.pdfgen import canvas
from PyPDF2 import PdfReader, PdfWriter

# ---------------------------------
# Step 1: Create normal PDF
# ---------------------------------
normal_pdf = "normal.pdf"

c = canvas.Canvas(normal_pdf)

c.drawString(
    100,
    750,
    "This is a password protected PDF test file."
)

c.save()

# ---------------------------------
# Step 2: Encrypt PDF
# ---------------------------------
reader = PdfReader(normal_pdf)

writer = PdfWriter()

for page in reader.pages:
    writer.add_page(page)

# Password
writer.encrypt("test123")

protected_pdf = "password_protected.pdf"

with open(protected_pdf, "wb") as f:
    writer.write(f)

print(f"Created: {protected_pdf}")
print("Password: test123")