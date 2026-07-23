(function () {
  "use strict";

  var adminToken = null;

  async function request(url, options) {
    if (!adminToken) {
      adminToken = window.prompt("请输入管理令牌");
    }
    if (!adminToken) {
      throw new Error("已取消管理操作");
    }

    var requestOptions = Object.assign({}, options || {});
    requestOptions.method = requestOptions.method || "POST";
    requestOptions.headers = Object.assign({}, requestOptions.headers || {}, {
      "Authorization": "Bearer " + adminToken
    });

    var response = await window.fetch(url, requestOptions);
    if (response.status === 401) {
      adminToken = null;
    }
    return response;
  }

  window.adminApi = {
    request: request,
    clearToken: function () { adminToken = null; }
  };
}());
