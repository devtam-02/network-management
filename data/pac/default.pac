
var PROXY_SERVER = "PROXY 10.254.148.131:8800";

function FindProxyForURL(url, host) {

    if (isPlainHostName(host)
        || shExpMatch(host, "*.local")
        || isInNet(dnsResolve(host), "127.0.0.0", "255.0.0.0")
        || isInNet(dnsResolve(host), "10.0.0.0", "255.0.0.0")
        || isInNet(dnsResolve(host), "172.16.0.0", "255.240.0.0")
        || isInNet(dnsResolve(host), "192.168.0.0", "255.255.0.0")) {
        return "DIRECT";
    }

    if (dnsDomainIs(host, ".viettelmoney.vn") || host === "jira.viettelmoney.vn") {
        return PROXY_SERVER;
    }

    return "DIRECT";
}
