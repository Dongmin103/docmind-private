function Send-SignedJson {
    param([string]$Path,[string]$Json)
    $uri=[Uri]::new($base,$Path)
    if(-not $uri.IsLoopback -or $uri.Authority -ne $base.Authority) { throw 'SYNC_ENDPOINT_ESCAPED' }
    $bytes=[Text.Encoding]::UTF8.GetBytes($Json);$hash=Get-DocMindBytesSha256 -Bytes $bytes
    $stamp=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString();$nonce=ConvertTo-DocMindHex -Bytes (New-DocMindRandomBytes -Count 16)
    $sig=Get-DocMindHmacSignature -Key $secret -Method POST -PathAndQuery $uri.PathAndQuery -Timestamp $stamp -Nonce $nonce -ContentSha256 $hash
    $request=[Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Post,$uri)
    $request.Content=[Net.Http.ByteArrayContent]::new($bytes)
    $request.Content.Headers.ContentType=[Net.Http.Headers.MediaTypeHeaderValue]::new('application/json')
    foreach($pair in @{'X-DocMind-Key-Id'=[string]$config.key_id;'X-DocMind-Timestamp'=$stamp;'X-DocMind-Nonce'=$nonce;'X-DocMind-Content-SHA256'=$hash;'X-DocMind-Signature'=$sig}.GetEnumerator()) { [void]$request.Headers.TryAddWithoutValidation($pair.Key,$pair.Value) }
    $client=[Net.Http.HttpClient]::new();$client.Timeout=[TimeSpan]::FromSeconds(30)
    try {
        $reply=$client.SendAsync($request).GetAwaiter().GetResult()
        try {
            $data=$reply.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult();$headers=@{}
            foreach($name in @('X-DocMind-Key-Id','X-DocMind-Timestamp','X-DocMind-Nonce','X-DocMind-Content-SHA256','X-DocMind-Signature')) {
                $values=$null;if(-not $reply.Headers.TryGetValues($name,[ref]$values)) { throw 'SYNC_RESPONSE_UNSIGNED' };$headers[$name]=[string]@($values)[0]
            }
            if($headers['X-DocMind-Key-Id'] -ne [string]$config.key_id -or $nonces.ContainsKey($headers['X-DocMind-Nonce']) -or -not (Test-DocMindSignedMessage -Key $secret -Method POST -PathAndQuery $uri.PathAndQuery -Timestamp $headers['X-DocMind-Timestamp'] -Nonce $headers['X-DocMind-Nonce'] -ContentSha256 $headers['X-DocMind-Content-SHA256'] -Signature $headers['X-DocMind-Signature'] -BodyBytes $data)) { throw 'SYNC_RESPONSE_AUTH_FAILED' }
            $nonces[$headers['X-DocMind-Nonce']]=[DateTimeOffset]::UtcNow
            foreach($seen in @($nonces.GetEnumerator())) { if($seen.Value -lt [DateTimeOffset]::UtcNow.AddMinutes(-5)) { $nonces.Remove($seen.Key) } }
            $body=if($data.Length) { [Text.Encoding]::UTF8.GetString($data)|ConvertFrom-Json } else { $null }
            return [pscustomobject]@{Status=[int]$reply.StatusCode;Body=$body}
        } finally { $reply.Dispose() }
    } finally { $request.Dispose();$client.Dispose() }
}
