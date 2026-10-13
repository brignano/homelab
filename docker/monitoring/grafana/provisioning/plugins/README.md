# Plugin provisioning

Empty on purpose. Grafana scans this directory on a timer and logs
`level=error msg="Failed to read plugin provisioning files from directory"`
every time it is missing, which showed up in the digest's log error count.
Drop a plugin provisioning YAML here if one is ever needed.
