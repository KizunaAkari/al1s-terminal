#include "NativeApi.h"
#include <memory>

namespace {
struct StringBuffer {
    MaaStringBuffer* value = maaApi<decltype(&MaaStringBufferCreate)>("MaaStringBufferCreate")();
    ~StringBuffer() { maaApi<decltype(&MaaStringBufferDestroy)>("MaaStringBufferDestroy")(value); }
    const char* data() { return maaApi<decltype(&MaaStringBufferGet)>("MaaStringBufferGet")(value); }
};
bool available(JNIEnv* env) {
    for (auto name : {"MaaStringBufferCreate", "MaaStringBufferDestroy", "MaaStringBufferGet", "MaaContextRunRecognition",
        "MaaContextRunAction", "MaaContextGetTasker", "MaaTaskerGetRecognitionDetail", "MaaTaskerGetActionDetail",
        "MaaTaskerGetController", "MaaImageBufferCreate", "MaaImageBufferDestroy", "MaaControllerPostScreencap",
        "MaaControllerWait", "MaaControllerCachedImage", "MaaImageBufferWidth", "MaaImageBufferHeight",
        "MaaContextGetNodeData", "MaaTaskerStopping"}) {
        if (!al1sFrameworkApi(name)) { maaError(env, "Maa context API unavailable"); return false; }
    }
    return true;
}
}

extern "C" JNIEXPORT jobject JNICALL
Java_com_al1s_terminal_maa_NativeContext_recognize(JNIEnv* env, jobject, jlong handle, jstring source, jlong image) {
    if (!handle || !image || !available(env)) return nullptr;
    auto context = reinterpret_cast<MaaContext*>(handle);
    auto id = maaApi<decltype(&MaaContextRunRecognition)>("MaaContextRunRecognition")(context,maaText(env,source).c_str(),"{}",
        reinterpret_cast<MaaImageBuffer*>(image));
    auto tasker = maaApi<decltype(&MaaContextGetTasker)>("MaaContextGetTasker")(context);
    StringBuffer name, algorithm, details;
    MaaBool hit = false; MaaRect box {};
    if (!maaApi<decltype(&MaaTaskerGetRecognitionDetail)>("MaaTaskerGetRecognitionDetail")(tasker,id,
        name.value,algorithm.value,&hit,&box,details.value,nullptr,nullptr)) hit = false;
    auto array = env->NewIntArray(4);
    jint values[] = {box.x,box.y,box.width,box.height};
    env->SetIntArrayRegion(array,0,4,values);
    auto body = env->NewStringUTF(details.data());
    auto type = env->FindClass("com/al1s/terminal/maa/NativeRecognitionResult");
    auto constructor = env->GetMethodID(type,"<init>","(Z[ILjava/lang/String;)V");
    auto result = env->NewObject(type,constructor,static_cast<jboolean>(hit),array,body);
    env->DeleteLocalRef(type); env->DeleteLocalRef(array); env->DeleteLocalRef(body);
    return result;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_al1s_terminal_maa_NativeContext_action(JNIEnv* env, jobject, jlong handle, jstring source, jintArray rectangle, jstring detail) {
    if (!handle || !available(env) || env->GetArrayLength(rectangle) != 4) return false;
    auto context = reinterpret_cast<MaaContext*>(handle);
    jint values[4]; env->GetIntArrayRegion(rectangle,0,4,values);
    MaaRect box {values[0],values[1],values[2],values[3]};
    auto id = maaApi<decltype(&MaaContextRunAction)>("MaaContextRunAction")(context,maaText(env,source).c_str(),"{}",&box,
        maaText(env,detail).c_str());
    auto tasker = maaApi<decltype(&MaaContextGetTasker)>("MaaContextGetTasker")(context);
    StringBuffer name, action, details; MaaBool success = false;
    return maaApi<decltype(&MaaTaskerGetActionDetail)>("MaaTaskerGetActionDetail")(tasker,id,name.value,action.value,&box,&success,details.value)
        && success;
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_al1s_terminal_maa_NativeContext_capture(JNIEnv* env, jobject, jlong handle) {
    if (!handle || !available(env)) return 0;
    auto context = reinterpret_cast<MaaContext*>(handle);
    auto tasker = maaApi<decltype(&MaaContextGetTasker)>("MaaContextGetTasker")(context);
    auto controller = maaApi<decltype(&MaaTaskerGetController)>("MaaTaskerGetController")(tasker);
    auto id = maaApi<decltype(&MaaControllerPostScreencap)>("MaaControllerPostScreencap")(controller);
    if (maaApi<decltype(&MaaControllerWait)>("MaaControllerWait")(controller,id) != MaaStatus_Succeeded) return 0;
    auto image = maaApi<decltype(&MaaImageBufferCreate)>("MaaImageBufferCreate")();
    if (!image) return 0;
    if (!maaApi<decltype(&MaaControllerCachedImage)>("MaaControllerCachedImage")(controller,image)) {
        maaApi<decltype(&MaaImageBufferDestroy)>("MaaImageBufferDestroy")(image); return 0;
    }
    return reinterpret_cast<jlong>(image);
}

extern "C" JNIEXPORT jintArray JNICALL
Java_com_al1s_terminal_maa_NativeContext_imageSize(JNIEnv* env, jobject, jlong handle) {
    if (!handle || !available(env)) return nullptr;
    auto image = reinterpret_cast<MaaImageBuffer*>(handle);
    jint values[] = {maaApi<decltype(&MaaImageBufferWidth)>("MaaImageBufferWidth")(image),
        maaApi<decltype(&MaaImageBufferHeight)>("MaaImageBufferHeight")(image)};
    auto result = env->NewIntArray(2); env->SetIntArrayRegion(result,0,2,values); return result;
}
extern "C" JNIEXPORT jlong JNICALL
Java_com_al1s_terminal_maa_NativeContext_imageFromEncoded(JNIEnv* env, jobject, jbyteArray bytes) {
    if (!bytes || !available(env)) return 0;
    auto set = maaApi<decltype(&MaaImageBufferSetEncoded)>("MaaImageBufferSetEncoded");
    auto create = maaApi<decltype(&MaaImageBufferCreate)>("MaaImageBufferCreate");
    auto destroy = maaApi<decltype(&MaaImageBufferDestroy)>("MaaImageBufferDestroy");
    if (!set) { maaError(env,"Maa image decoding API unavailable"); return 0; }
    auto count = env->GetArrayLength(bytes);
    if (count < 24 || count > 4 * 1024 * 1024) return 0;
    auto data = env->GetByteArrayElements(bytes,nullptr);
    auto image = create();
    auto ok = set(image,reinterpret_cast<uint8_t*>(data),static_cast<MaaSize>(count));
    env->ReleaseByteArrayElements(bytes,data,JNI_ABORT);
    if (!ok) { destroy(image); return 0; }
    return reinterpret_cast<jlong>(image);
}
extern "C" JNIEXPORT void JNICALL
Java_com_al1s_terminal_maa_NativeContext_destroyImage(JNIEnv* env, jobject, jlong handle) {
    if (handle && available(env)) maaApi<decltype(&MaaImageBufferDestroy)>("MaaImageBufferDestroy")(reinterpret_cast<MaaImageBuffer*>(handle));
}
extern "C" JNIEXPORT jbyteArray JNICALL
Java_com_al1s_terminal_maa_NativeContext_encodedImage(JNIEnv* env, jobject, jlong handle) {
    if (!handle || !available(env)) return nullptr;
    auto image = reinterpret_cast<MaaImageBuffer*>(handle);
    auto get = maaApi<decltype(&MaaImageBufferGetEncoded)>("MaaImageBufferGetEncoded");
    auto size = maaApi<decltype(&MaaImageBufferGetEncodedSize)>("MaaImageBufferGetEncodedSize");
    if (!get || !size) { maaError(env,"Maa image encoding API unavailable"); return nullptr; }
    auto data = get(image); auto length = size(image);
    if (!data || length == 0 || length > 32*1024*1024) { maaError(env,"Maa encoded frame invalid"); return nullptr; }
    auto result = env->NewByteArray(static_cast<jsize>(length));
    if (result) env->SetByteArrayRegion(result,0,static_cast<jsize>(length),reinterpret_cast<const jbyte*>(data));
    return result;
}
extern "C" JNIEXPORT jstring JNICALL
Java_com_al1s_terminal_maa_NativeContext_nodeData(JNIEnv* env, jobject, jlong handle, jstring source) {
    if (!handle || !available(env)) return nullptr;
    StringBuffer buffer;
    if (!maaApi<decltype(&MaaContextGetNodeData)>("MaaContextGetNodeData")(reinterpret_cast<MaaContext*>(handle),maaText(env,source).c_str(),buffer.value)) {
        maaError(env,"Maa delegated node unavailable"); return nullptr;
    }
    return env->NewStringUTF(buffer.data());
}
extern "C" JNIEXPORT jboolean JNICALL
Java_com_al1s_terminal_maa_NativeContext_stopping(JNIEnv* env, jobject, jlong handle) {
    if (!handle || !available(env)) return true;
    auto tasker = maaApi<decltype(&MaaContextGetTasker)>("MaaContextGetTasker")(reinterpret_cast<MaaContext*>(handle));
    return maaApi<decltype(&MaaTaskerStopping)>("MaaTaskerStopping")(tasker);
}
extern "C" JNIEXPORT jboolean JNICALL
Java_com_al1s_terminal_maa_NativeContext_runTask(JNIEnv* env,jobject,jlong handle,jstring entry,jstring pipeline) {
    if(!handle || !available(env))return false;
    auto run=maaApi<decltype(&MaaContextRunTask)>("MaaContextRunTask");
    auto detail=maaApi<decltype(&MaaTaskerGetTaskDetail)>("MaaTaskerGetTaskDetail");
    if(!run || !detail) {maaError(env,"Maa nested task API unavailable");return false;}
    auto context=reinterpret_cast<MaaContext*>(handle);
    auto id=run(context,maaText(env,entry).c_str(),maaText(env,pipeline).c_str());
    auto tasker=maaApi<decltype(&MaaContextGetTasker)>("MaaContextGetTasker")(context);
    StringBuffer name;MaaSize count=0;MaaStatus status=MaaStatus_Invalid;
    return detail(tasker,id,name.value,nullptr,&count,&status) && status==MaaStatus_Succeeded;
}
extern "C" JNIEXPORT jboolean JNICALL
Java_com_al1s_terminal_maa_NativeContext_clearHitCount(JNIEnv* env,jobject,jlong handle,jstring node) {
    if(!handle || !available(env))return false;
    auto clear=maaApi<decltype(&MaaContextClearHitCount)>("MaaContextClearHitCount");
    if(!clear) {maaError(env,"Maa hit-count API unavailable");return false;}
    return clear(reinterpret_cast<MaaContext*>(handle),maaText(env,node).c_str());
}
