#define _POSIX_C_SOURCE 200809L

#include "server.h"
#include "input.h"
#include "platform.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define REQUEST_BUFFER_SIZE 65536

static const char PAGE[] =
"<!doctype html>"
"<html>"
"<head>"
"<meta charset=\"utf-8\">"
"<meta name=\"viewport\" "
"content=\"width=device-width,initial-scale=1\">"
"<title>NamakiOS</title>"
"<style>"
"html,body{"
"margin:0;"
"width:100%;"
"height:100%;"
"overflow:hidden;"
"background:#000;"
"}"
"canvas{"
"display:block;"
"width:100vw;"
"height:100vh;"
"object-fit:contain;"
"background:#000;"
"touch-action:none;"
"}"
"</style>"
"</head>"
"<body>"
"<canvas id=\"screen\" "
"width=\"1024\" height=\"768\"></canvas>"
"<script>"
"const screen=document.getElementById('screen');"
"const ctx=screen.getContext('2d');"
"const base=location.origin;"
"let stopped=false;"
"let lastMove=0;"
"let queue=[];"
"let sending=false;"

"function position(e){"
"const r=screen.getBoundingClientRect();"
"return{"
"x:Math.max(0,Math.min(1023,"
"(e.clientX-r.left)*1024/r.width)),"
"y:Math.max(0,Math.min(767,"
"(e.clientY-r.top)*768/r.height))"
"};"
"}"

"async function sendInput(){"
"if(sending||queue.length===0)return;"
"sending=true;"
"const batch=queue.splice(0,100);"
"try{"
"await fetch(base+'/input',{"
"method:'POST',"
"headers:{'Content-Type':'application/json'},"
"body:JSON.stringify(batch)"
"});"
"}catch(e){"
"queue.unshift(...batch);"
"}finally{"
"sending=false;"
"if(queue.length)sendInput();"
"}"
"}"

"function push(value){"
"queue.push(value);"
"if(queue.length>=20)sendInput();"
"}"

"screen.addEventListener('pointermove',e=>{"
"const now=performance.now();"
"if(now-lastMove<8)return;"
"lastMove=now;"
"push({"
"type:'move',"
"...position(e)"
"});"
"});"

"screen.addEventListener('pointerdown',e=>{"
"const p=position(e);"
"push({"
"type:'move',"
"...p"
"});"
"push({"
"type:'button',"
"button:e.button,"
"down:true"
"});"
"sendInput();"
"});"

"screen.addEventListener('pointerup',e=>{"
"push({"
"type:'button',"
"button:e.button,"
"down:false"
"});"
"sendInput();"
"});"

"screen.addEventListener('wheel',e=>{"
"push({"
"type:'wheel',"
"up:e.deltaY<0"
"});"
"sendInput();"
"e.preventDefault();"
"},{passive:false});"

"const keyMap={"
"Enter:'enter',"
"Escape:'esc',"
"Tab:'tab',"
"Backspace:'backspace',"
"Delete:'delete',"
"ArrowUp:'up',"
"ArrowDown:'down',"
"ArrowLeft:'left',"
"ArrowRight:'right',"
"Home:'home',"
"End:'end',"
"PageUp:'pgup',"
"PageDown:'pgdn',"
"Insert:'insert',"
"Space:'spc',"
"Shift:'shift',"
"Control:'ctrl',"
"Alt:'alt',"
"Meta:'meta',"
"CapsLock:'caps_lock',"
"F1:'f1',"
"F2:'f2',"
"F3:'f3',"
"F4:'f4',"
"F5:'f5',"
"F6:'f6',"
"F7:'f7',"
"F8:'f8',"
"F9:'f9',"
"F10:'f10',"
"F11:'f11',"
"F12:'f12'"
"};"

"function keyName(e){"
"if(keyMap[e.key])return keyMap[e.key];"
"if(e.key.length===1)return e.key.toLowerCase();"
"return null;"
"}"

"window.addEventListener('keydown',e=>{"
"const key=keyName(e);"
"if(!key)return;"
"push({"
"type:'key',"
"key:key,"
"down:true"
"});"
"sendInput();"
"e.preventDefault();"
"});"

"window.addEventListener('keyup',e=>{"
"const key=keyName(e);"
"if(!key)return;"
"push({"
"type:'key',"
"key:key,"
"down:false"
"});"
"sendInput();"
"e.preventDefault();"
"});"

"window.addEventListener('blur',()=>{"
"for(const key of ["
"'shift','ctrl','alt','meta'"
"]){"
"push({"
"type:'key',"
"key:key,"
"down:false"
"});"
"}"
"sendInput();"
"});"

"async function frame(){"
"while(!stopped){"
"try{"
"const r=await fetch(base+'/frame',{"
"cache:'no-store'"
"});"
"if(!r.ok)throw Error();"
"const bitmap=await createImageBitmap("
"await r.blob()"
");"
"ctx.drawImage(bitmap,0,0);"
"bitmap.close();"
"}catch(e){"
"await new Promise(r=>"
"setTimeout(r,100));"
"}"
"}"
"}"

"setInterval(sendInput,8);"
"frame();"
"</script>"
"</body>"
"</html>";

static bool send_response(
    namaki_socket_t socket,
    int status,
    const char *type,
    const void *body,
    size_t body_size
)
{
    const char *message =
        status == 200
            ? "OK"
            : status == 404
                ? "Not Found"
                : "Bad Request";

    char header[1024];

    int length = snprintf(
        header,
        sizeof(header),
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %zu\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "\r\n",
        status,
        message,
        type,
        body_size
    );

    if (
        length < 0 ||
        (size_t)length >= sizeof(header)
    )
        return false;

    if (!platform_send_all(
            socket,
            header,
            (size_t)length
        ))
        return false;

    if (
        body_size > 0 &&
        !platform_send_all(
            socket,
            body,
            body_size
        )
    )
        return false;

    return true;
}

static bool read_request(
    namaki_socket_t socket,
    char **output,
    size_t *output_size
)
{
    size_t capacity = 8192;
    size_t length = 0;

    char *buffer = malloc(
        capacity
    );

    if (buffer == NULL)
        return false;

    while (true) {
        if (length + 4096 + 1 > capacity) {
            capacity *= 2;

            if (capacity > 1024 * 1024) {
                free(buffer);
                return false;
            }

            char *grown = realloc(
                buffer,
                capacity
            );

            if (grown == NULL) {
                free(buffer);
                return false;
            }

            buffer = grown;
        }

        size_t received = 0;

        while (received < 4096) {
            char c;

            if (!platform_recv_byte(
                    socket,
                    &c
                ))
                break;

            buffer[length++] = c;
            received++;

            if (
                length >= 4 &&
                memcmp(
                    buffer + length - 4,
                    "\r\n\r\n",
                    4
                ) == 0
            )
                goto headers_done;
        }

        if (received == 0)
            break;
    }

headers_done:

    buffer[length] = '\0';

    *output = buffer;
    *output_size = length;

    return true;
}

static bool get_content_length(
    const char *request,
    size_t *length
)
{
    const char *header =
        strstr(
            request,
            "Content-Length:"
        );

    if (header == NULL)
        header = strstr(
            request,
            "content-length:"
        );

    if (header == NULL) {
        *length = 0;
        return true;
    }

    header += 15;

    while (
        *header == ' ' ||
        *header == '\t'
    )
        header++;

    char *end;

    unsigned long value =
        strtoul(
            header,
            &end,
            10
        );

    if (end == header)
        return false;

    *length = (size_t)value;

    return true;
}

static void handle_client(
    Server *server,
    namaki_socket_t client
)
{
    char *request = NULL;
    size_t request_size = 0;

    if (!read_request(
            client,
            &request,
            &request_size
        )) {
        platform_socket_close(
            client
        );

        return;
    }

    (void)request_size;

    char method[16];
    char path[256];

    if (
        sscanf(
            request,
            "%15s %255s",
            method,
            path
        ) != 2
    ) {
        send_response(
            client,
            400,
            "text/plain",
            "Bad Request",
            11
        );

        free(request);

        platform_socket_close(
            client
        );

        return;
    }

    if (
        strcmp(
            path,
            "/namaki"
        ) == 0 &&
        strcmp(
            method,
            "GET"
        ) == 0
    ) {
        send_response(
            client,
            200,
            "text/html; charset=utf-8",
            PAGE,
            sizeof(PAGE) - 1
        );
    }
    else if (
        strcmp(
            path,
            "/frame"
        ) == 0 &&
        strcmp(
            method,
            "GET"
        ) == 0
    ) {
        unsigned char *frame = NULL;
        size_t frame_size = 0;

        if (
            display_get_frame(
                server->display,
                &frame,
                &frame_size
            )
        ) {
            send_response(
                client,
                200,
                "image/png",
                frame,
                frame_size
            );

            free(frame);
        }
        else {
            send_response(
                client,
                404,
                "text/plain",
                "No frame",
                8
            );
        }
    }
    else if (
        strcmp(
            path,
            "/input"
        ) == 0 &&
        strcmp(
            method,
            "POST"
        ) == 0
    ) {
        size_t content_length = 0;

        if (
            !get_content_length(
                request,
                &content_length
            ) ||
            content_length > 1024 * 1024
        ) {
            send_response(
                client,
                400,
                "text/plain",
                "Invalid body",
                13
            );
        }
        else {
            const char *body =
                strstr(
                    request,
                    "\r\n\r\n"
                );

            if (body == NULL) {
                send_response(
                    client,
                    400,
                    "text/plain",
                    "Invalid request",
                    15
                );
            }
            else {
                body += 4;

                size_t header_body =
                    (size_t)(
                        request + request_size
                        - body
                    );

                char *complete =
                    malloc(
                        content_length + 1
                    );

                if (complete == NULL) {
                    send_response(
                        client,
                        400,
                        "text/plain",
                        "Out of memory",
                        14
                    );
                }
                else {
                    size_t copied =
                        header_body <
                            content_length
                            ? header_body
                            : content_length;

                    memcpy(
                        complete,
                        body,
                        copied
                    );

                    size_t remaining =
                        content_length - copied;

                    if (
                        remaining > 0 &&
                        !platform_recv_all(
                            client,
                            complete + copied,
                            remaining
                        )
                    ) {
                        free(complete);

                        platform_socket_close(
                            client
                        );

                        free(request);

                        return;
                    }

                    complete[
                        content_length
                    ] = '\0';

                    bool success =
                        input_process(
                            server->qmp,
                            complete
                        );

                    free(complete);

                    if (success) {
                        send_response(
                            client,
                            200,
                            "text/plain",
                            "OK",
                            2
                        );
                    }
                    else {
                        send_response(
                            client,
                            400,
                            "text/plain",
                            "Invalid input",
                            13
                        );
                    }
                }
            }
        }
    }
    else {
        send_response(
            client,
            404,
            "text/plain",
            "Not Found",
            9
        );
    }

    free(request);

    platform_socket_close(
        client
    );
}

static void *server_thread(
    void *argument
)
{
    Server *server =
        argument;

    while (server->running) {
        namaki_socket_t client =
            platform_tcp_accept(
                server->fd
            );

        if (
            client ==
            NAMAKI_INVALID_SOCKET
        ) {
            if (server->running)
                continue;

            break;
        }

        handle_client(
            server,
            client
        );
    }

    return NULL;
}

bool server_start(
    Server *server,
    Display *display,
    Qmp *qmp,
    const char *token
)
{
    memset(
        server,
        0,
        sizeof(*server)
    );

    server->fd =
        NAMAKI_INVALID_SOCKET;

    server->display = display;
    server->qmp = qmp;

    snprintf(
        server->token,
        sizeof(server->token),
        "%s",
        token != NULL
            ? token
            : ""
    );

    int port = 0;

    server->fd =
        platform_tcp_listen(
            0,
            16,
            &port
        );

    if (
        server->fd ==
        NAMAKI_INVALID_SOCKET
    )
        return false;

    server->port = port;
    server->running = true;

    if (
        !platform_thread_create(
            &server->thread,
            server_thread,
            server
        )
    ) {
        server->running = false;

        server->fd =
            platform_socket_close(
                server->fd
            );

        return false;
    }

    return true;
}

void server_stop(
    Server *server
)
{
    if (
        server == NULL ||
        !server->running
    )
        return;

    server->running = false;

    /*
     * Closing the listening socket wakes
     * the accept call on both supported
     * platform families.
     */
    server->fd =
        platform_socket_close(
            server->fd
        );

    platform_thread_join(
        server->thread
    );
}

int server_port(
    Server *server
)
{
    if (server == NULL)
        return 0;

    return server->port;
}