// Cấu hình proxy mặc định của netmgr.
//
// LƯU Ý về việc chọn hàm — đây là chỗ dễ sai nhất và sai thì KHÔNG báo lỗi gì,
// PAC vẫn chạy và chỉ trả về kết quả không như mong đợi:
//
//   so khớp TÊN MIỀN  ->  dnsDomainIs(host, ".viettel.com.vn")
//                         shExpMatch(host, "*.viettel.com.vn")
//   so khớp DẢI IP    ->  isInNet(dnsResolve(host), "10.0.0.0", "255.0.0.0")
//
// `isInNet` so sánh ĐỊA CHỈ IP với địa chỉ mạng + netmask. Truyền tên miền hoặc
// wildcard vào nó, ví dụ isInNet(host, "*.viettel.com.vn", "255.255.255.0"),
// sẽ cho kết quả vô nghĩa: đo trên máy này thì google.com bị đẩy qua proxy còn
// vas.viettel.com.vn lại đi thẳng — đúng ngược lại ý muốn.
//
// Giá trị trả về:
//   "DIRECT"                     đi thẳng, không qua proxy
//   "PROXY 10.0.0.1:8800"        đi qua proxy này
//   "PROXY 10.0.0.1:8800; DIRECT" thử proxy trước, không được thì đi thẳng

var PROXY_SERVER = "PROXY 10.254.148.131:8800";

function FindProxyForURL(url, host) {
    // Tên máy không có dấu chấm (vd "wiki") và dải nội bộ: luôn đi thẳng.
    // Đẩy qua proxy chỉ làm chậm và dễ hỏng.
    if (isPlainHostName(host)
        || shExpMatch(host, "*.local")
        || isInNet(dnsResolve(host), "127.0.0.0", "255.0.0.0")
        || isInNet(dnsResolve(host), "10.0.0.0", "255.0.0.0")
        || isInNet(dnsResolve(host), "172.16.0.0", "255.240.0.0")
        || isInNet(dnsResolve(host), "192.168.0.0", "255.255.0.0")) {
        return "DIRECT";
    }

    // Các miền cần đi qua proxy.
    if (dnsDomainIs(host, ".viettel.com.vn") || host === "viettel.com.vn") {
        return PROXY_SERVER;
    }

    // Còn lại đi thẳng.
    return "DIRECT";
}
