#include "NativeApi.h"
#include "MaaCallbacks.h"
#include <cstring>
#include <memory>

namespace {
struct Task {
    MaaResource* resource = nullptr;
    MaaTasker* tasker = nullptr;
    MaaTaskId id = 0;
    MaaTaskId stop_id = 0;
    JavaVM* vm = nullptr;
    jobject sink = nullptr;
    jmethodID event = nullptr;
    std::unique_ptr<MaaCallbacks> callbacks;
    ~Task() {
        if (tasker) {
            // Status can become final before the worker sends its final event.
            // Wait for the queued stop to finish before destroying dispatchers.
            if (id != 0) {
                if (!stop_id) stop_id = maaApi<decltype(&MaaTaskerPostStop)>("MaaTaskerPostStop")(tasker);
                maaApi<decltype(&MaaTaskerWait)>("MaaTaskerWait")(tasker, stop_id);
            }
            maaApi<decltype(&MaaTaskerClearSinks)>("MaaTaskerClearSinks")(tasker);
            maaApi<decltype(&MaaTaskerClearContextSinks)>("MaaTaskerClearContextSinks")(tasker);
            maaApi<decltype(&MaaTaskerDestroy)>("MaaTaskerDestroy")(tasker);
        }
        if (resource) maaApi<decltype(&MaaResourceDestroy)>("MaaResourceDestroy")(resource);
    }
};
void sink(void*, const char* message, const char* details, void* argument) {
    auto task = static_cast<Task*>(argument);
    JNIEnv* env = nullptr;
    bool attached = task->vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) == JNI_EDETACHED;
    if (attached && task->vm->AttachCurrentThread(&env, nullptr) != JNI_OK) return;
    if (!env) return;
    if (message && details && std::strlen(details) <= 262144) {
        auto event = env->NewStringUTF(message), body = env->NewStringUTF(details);
        if (event && body) env->CallVoidMethod(task->sink, task->event, event, body);
        if (env->ExceptionCheck()) env->ExceptionClear();
        if (event) env->DeleteLocalRef(event);
        if (body) env->DeleteLocalRef(body);
    }
    if (attached) task->vm->DetachCurrentThread();
}
bool available() {
    for (auto name : {"MaaResourceCreate", "MaaResourceDestroy", "MaaResourcePostBundle", "MaaResourceWait",
        "MaaTaskerCreate", "MaaTaskerDestroy", "MaaTaskerBindResource", "MaaTaskerBindController", "MaaTaskerInited",
        "MaaTaskerAddSink", "MaaTaskerAddContextSink", "MaaTaskerPostTask", "MaaTaskerStatus", "MaaTaskerPostStop",
        "MaaTaskerWait", "MaaTaskerClearSinks", "MaaTaskerClearContextSinks"})
        if (!al1sFrameworkApi(name)) return false;
    return true;
}
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_al1s_terminal_maa_NativeMaa_startTask(JNIEnv* env, jobject, jlong controller, jstring directory,
    jstring entry, jstring pipeline, jobject projection, jobject handlers, jstring ocr_path) {
    if (!controller || !available()) { maaError(env, "Maa task API unavailable"); return 0; }
    auto task = std::make_unique<Task>();
    task->resource = maaApi<decltype(&MaaResourceCreate)>("MaaResourceCreate")();
    task->tasker = maaApi<decltype(&MaaTaskerCreate)>("MaaTaskerCreate")();
    if (!task->resource || !task->tasker) { maaError(env,"Maa task allocation failed"); return 0; }
    if (handlers) {
        task->callbacks = MaaCallbacks::registerHandlers(env,task->resource,handlers);
        if (!task->callbacks) return 0;
    }
    auto ocr = maaText(env,ocr_path);
    if (!ocr.empty()) {
        auto post_ocr = maaApi<decltype(&MaaResourcePostOcrModel)>("MaaResourcePostOcrModel");
        if (!post_ocr || maaApi<decltype(&MaaResourceWait)>("MaaResourceWait")(task->resource,post_ocr(task->resource,ocr.c_str())) != MaaStatus_Succeeded) {
            maaError(env,"Maa CPU OCR model initialization failed"); return 0;
        }
    }
    auto id = maaApi<decltype(&MaaResourcePostBundle)>("MaaResourcePostBundle")(task->resource, maaText(env,directory).c_str());
    if (maaApi<decltype(&MaaResourceWait)>("MaaResourceWait")(task->resource,id) != MaaStatus_Succeeded ||
        !maaApi<decltype(&MaaTaskerBindResource)>("MaaTaskerBindResource")(task->tasker,task->resource) ||
        !maaApi<decltype(&MaaTaskerBindController)>("MaaTaskerBindController")(task->tasker,reinterpret_cast<MaaController*>(controller)) ||
        !maaApi<decltype(&MaaTaskerInited)>("MaaTaskerInited")(task->tasker)) {
        maaError(env,"Maa task resource initialization failed"); return 0;
    }
    env->GetJavaVM(&task->vm);
    auto type = env->GetObjectClass(projection);
    task->event = env->GetMethodID(type,"onEvent","(Ljava/lang/String;Ljava/lang/String;)V");
    env->DeleteLocalRef(type);
    if (!task->event) return 0;
    task->sink = env->NewGlobalRef(projection);
    maaApi<decltype(&MaaTaskerAddSink)>("MaaTaskerAddSink")(task->tasker,sink,task.get());
    maaApi<decltype(&MaaTaskerAddContextSink)>("MaaTaskerAddContextSink")(task->tasker,sink,task.get());
    task->id = maaApi<decltype(&MaaTaskerPostTask)>("MaaTaskerPostTask")(task->tasker,
        maaText(env,entry).c_str(),maaText(env,pipeline).c_str());
    return reinterpret_cast<jlong>(task.release());
}

extern "C" JNIEXPORT jint JNICALL
Java_com_al1s_terminal_maa_NativeMaa_taskStatus(JNIEnv*, jobject, jlong handle) {
    auto task = reinterpret_cast<Task*>(handle);
    return task ? maaApi<decltype(&MaaTaskerStatus)>("MaaTaskerStatus")(task->tasker,task->id) : MaaStatus_Invalid;
}
extern "C" JNIEXPORT void JNICALL
Java_com_al1s_terminal_maa_NativeMaa_stopTask(JNIEnv*, jobject, jlong handle) {
    auto task = reinterpret_cast<Task*>(handle);
    if (task && !task->stop_id) task->stop_id = maaApi<decltype(&MaaTaskerPostStop)>("MaaTaskerPostStop")(task->tasker);
}
extern "C" JNIEXPORT void JNICALL
Java_com_al1s_terminal_maa_NativeMaa_destroyTask(JNIEnv* env, jobject, jlong handle) {
    auto task = reinterpret_cast<Task*>(handle);
    if (!task) return;
    auto projection = task->sink;
    delete task; // Joins native workers before releasing their Java callback.
    if (projection) env->DeleteGlobalRef(projection);
}
