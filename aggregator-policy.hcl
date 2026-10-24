path "oauth2/servers/*" {
  capabilities = ["create", "update", "read", "list"]
}

path "oauth2/auth-code-url" {
  capabilities = ["update"]
}

path "oauth2/creds/*" {
  capabilities = ["create", "update", "read"]
}
