// Cấu hình mặc định của netmgr — sửa file này để đổi cách chọn proxy.
//
// PAC (Proxy Auto-Config) là một hàm JavaScript. Trình duyệt và các ứng dụng
// tôn trọng cài đặt proxy của hệ thống sẽ gọi nó cho MỖI địa chỉ, rồi đi theo
// thứ tự nó trả về:
//
//   "DIRECT"                    đi thẳng, không qua proxy
//   "PROXY 10.0.0.1:3128"       đi qua proxy này
//   "PROXY a:3128; DIRECT"      thử proxy trước, không được thì đi thẳng
//
// Mặc định dưới đây cho MỌI thứ đi thẳng, tức chưa proxy gì cả. Hãy thay
// PROXY_SERVER và bỏ comment các nhánh bạn cần.

var PROXY_SERVER = "PROXY 127.0.0.1:3128";

function FindProxyForURL(url, host) {
    // Địa chỉ nội bộ: luôn đi thẳng. Đưa qua proxy chỉ làm chậm và dễ hỏng.
    if (isPlainHostName(host)
        || shExpMatch(host, "*.local")
        || isInNet(dnsResolve(host), "127.0.0.0", "255.0.0.0")
        || isInNet(dnsResolve(host), "10.0.0.0", "255.0.0.0")
        || isInNet(dnsResolve(host), "172.16.0.0", "255.240.0.0")
        || isInNet(dnsResolve(host), "192.168.0.0", "255.255.0.0")) {
        return "DIRECT";
    }

    // Ví dụ: chỉ định tuyến một số miền qua proxy.
    // if (shExpMatch(host, "*.example.com")) {
    //     return PROXY_SERVER;
    // }

    // Còn lại đi thẳng. Đổi thành PROXY_SERVER nếu muốn mọi thứ qua proxy.
    return "DIRECT";
}
