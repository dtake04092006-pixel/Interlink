# Sử dụng một ảnh nền Python gọn nhẹ
FROM python:3.10-slim

# Thiết lập thư mục làm việc bên trong container
WORKDIR /app

# Sao chép file requirements trước để tận dụng cache của Docker
COPY requirements.txt .

# Cài đặt các thư viện Python
RUN pip install --no-cache-dir -r requirements.txt

# Sao chép toàn bộ mã nguồn của bot vào container
COPY . .

# Lệnh để chạy ứng dụng của bạn khi container khởi động
CMD ["python", "Interlink.py"]
