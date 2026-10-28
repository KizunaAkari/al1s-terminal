#pragma once
#include <jni.h>
#include <string>
#include <MaaFramework/MaaAPI.h>

void* al1sFrameworkApi(const char* name);
template<class T> T maaApi(const char* name) { return reinterpret_cast<T>(al1sFrameworkApi(name)); }
inline void maaError(JNIEnv* env, const char* message) {
    auto type = env->FindClass("java/lang/IllegalStateException");
    if (type) { env->ThrowNew(type, message); env->DeleteLocalRef(type); }
}
inline std::string maaText(JNIEnv* env, jstring text) {
    if (!text) return {};
    auto type = env->FindClass("java/lang/String");
    auto method = env->GetMethodID(type,"getBytes","(Ljava/lang/String;)[B");
    auto charset = env->NewStringUTF("UTF-8");
    auto bytes = static_cast<jbyteArray>(env->CallObjectMethod(text,method,charset));
    env->DeleteLocalRef(charset); env->DeleteLocalRef(type);
    if (!bytes || env->ExceptionCheck()) return {};
    auto length = env->GetArrayLength(bytes);
    std::string result(static_cast<size_t>(length),'\0');
    env->GetByteArrayRegion(bytes,0,length,reinterpret_cast<jbyte*>(result.data()));
    env->DeleteLocalRef(bytes); return result;
}
