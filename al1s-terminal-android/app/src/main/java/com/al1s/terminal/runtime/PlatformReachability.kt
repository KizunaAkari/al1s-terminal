package com.al1s.terminal.runtime

import com.al1s.terminal.protocol.PlatformEndpoint
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString

data class ReachabilityIdentity(val origin:String,val terminalId:String,val credential:String)

/** Owned by the foreground service. A pulse is a network event, never a device operation. */
class PlatformReachability(
    private val identity:()->ReachabilityIdentity?,
    private val connect:(Request,WebSocketListener)->WebSocket,
    private val opened:()->Unit,
) : AutoCloseable {
    private class Attempt(val identity:ReachabilityIdentity) {var socket:WebSocket?=null}
    private var current:Attempt?=null
    private var stopped=false

    // Called from the service's single connection executor; callbacks may arrive on OkHttp threads.
    fun reconcile(networkChanged:Boolean=false) {
        val snapshot=identity()
        val attempt:Attempt?
        val previous:Attempt?
        synchronized(this) {
            if(stopped || (!networkChanged && current!=null && current?.identity==snapshot))return
            previous=current
            attempt=snapshot?.let(::Attempt)
            current=attempt
        }
        previous?.socket?.cancel()
        if(attempt==null)return
        try {
            val origin=PlatformEndpoint.normalize(attempt.identity.origin).replaceFirst("https://","wss://")
            val request=Request.Builder().url("$origin/api/v1/terminal/reachability")
                .header("Authorization","Bearer ${attempt.identity.credential}").build()
            val socket=connect(request,listener(attempt))
            synchronized(this) {
                if(current===attempt && !stopped)attempt.socket=socket else socket.cancel()
            }
        } catch(error:Exception) {lost(attempt);throw error}
    }

    @Synchronized private fun owns(attempt:Attempt)=!stopped && current===attempt
    @Synchronized private fun lost(attempt:Attempt) {if(current===attempt)current=null}

    private fun listener(attempt:Attempt)=object:WebSocketListener() {
        override fun onOpen(socket:WebSocket,response:Response) {
            if(owns(attempt))opened() else socket.cancel()
        }
        override fun onMessage(socket:WebSocket,text:String) {
            if(!owns(attempt) || text!="al1s-reachability-v1") {lost(attempt);socket.cancel()}
        }
        override fun onMessage(socket:WebSocket,bytes:ByteString) {lost(attempt);socket.cancel()}
        override fun onFailure(socket:WebSocket,error:Throwable,response:Response?) {lost(attempt)}
        override fun onClosing(socket:WebSocket,code:Int,reason:String) {socket.close(code,reason)}
        override fun onClosed(socket:WebSocket,code:Int,reason:String) {lost(attempt)}
    }

    override fun close() {
        val socket=synchronized(this) {stopped=true;current?.socket.also {current=null}}
        socket?.cancel()
    }
}
