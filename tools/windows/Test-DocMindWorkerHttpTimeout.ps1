[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Net.Http
Add-Type -TypeDefinition @'
using System;
using System.Net;
using System.Net.Sockets;
using System.IO;
using System.Text;
using System.Threading;
public class DocMindSlowHttp {
    public static int Start() {
        var listener=new TcpListener(IPAddress.Loopback,0);
        listener.Start();
        int port=((IPEndPoint)listener.LocalEndpoint).Port;
        var thread=new Thread(()=> {
            try {
                using(var client=listener.AcceptTcpClient())
                using(var stream=client.GetStream()) {
                    client.ReceiveTimeout=5000;
                    var reader=new StreamReader(stream,Encoding.ASCII);
                    while(!String.IsNullOrEmpty(reader.ReadLine())) {}
                    Thread.Sleep(1500);
                    byte[] data=Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}");
                    stream.Write(data,0,data.Length);
                }
            } catch(IOException) {} finally { listener.Stop(); }
        });
        thread.IsBackground=true;thread.Start();return port;
    }
}
'@
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'Start-DocMindUEncryptorHostWorker.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Worker parse failed.'}
$node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-SignedBytesRequest'},$false)
Invoke-Expression $node.Extent.Text
# Only authentication is replaced; exercise real HttpClient cancellation and
# response reads against a loopback server with a delayed response.
function New-SignedRequestHeaders {return @{}}
function Assert-SignedResponse {}
$apiBase=[Uri]('http://127.0.0.1:'+([DocMindSlowHttp]::Start()))
$caught=''
try {
    $content=[Net.Http.ByteArrayContent]::new([byte[]]@(123,125))
    Invoke-SignedBytesRequest -Method POST -RelativeUri '/validate' -ContentSha256 ('a'*64) -Content $content -ContentType 'application/json' -RequestTimeoutSeconds 1 | Out-Null
} catch {$caught=$_.Exception.Message}
if($caught -ne 'API_REQUEST_TIMEOUT'){throw ('Delayed request did not produce classified timeout: '+$caught)}
$apiBase=[Uri]('http://127.0.0.1:'+([DocMindSlowHttp]::Start()))
$content=[Net.Http.ByteArrayContent]::new([byte[]]@(123,125))
$result=Invoke-SignedBytesRequest -Method POST -RelativeUri '/validate' -ContentSha256 ('a'*64) -Content $content -ContentType 'application/json' -RequestTimeoutSeconds 10
if($result.StatusCode -ne 200 -or [Text.Encoding]::UTF8.GetString($result.Body) -ne '{}'){throw 'Delayed response failed within ingestion timeout.'}
'PASS: real delayed HTTP rejects the one-second budget with a safe timeout code and succeeds with the ingestion budget.'
