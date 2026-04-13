#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <avrt.h>
#include <objidl.h>
#include <propidl.h>

#if defined(__has_include)
#  if __has_include(<audioclientactivationparams.h>)
#    include <audioclientactivationparams.h>
#    define MIXSPLITR_HAS_AUDIOCLIENT_ACTIVATION_HEADER 1
#  endif
#endif

#if !defined(MIXSPLITR_HAS_AUDIOCLIENT_ACTIVATION_HEADER)
typedef enum AUDIOCLIENT_ACTIVATION_TYPE {
    AUDIOCLIENT_ACTIVATION_TYPE_DEFAULT = 0,
    AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
} AUDIOCLIENT_ACTIVATION_TYPE;

typedef enum PROCESS_LOOPBACK_MODE {
    PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0,
    PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1
} PROCESS_LOOPBACK_MODE;

typedef struct AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS {
    DWORD TargetProcessId;
    PROCESS_LOOPBACK_MODE ProcessLoopbackMode;
} AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS;

typedef struct AUDIOCLIENT_ACTIVATION_PARAMS {
    AUDIOCLIENT_ACTIVATION_TYPE ActivationType;
    union {
        AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS ProcessLoopbackParams;
    };
} AUDIOCLIENT_ACTIVATION_PARAMS;
#endif

#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <cwchar>
#include <cmath>
#include <string>
#include <vector>
#include <sstream>

#include <fcntl.h>
#include <io.h>

#ifndef VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK
#define VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK L"VAD\\Process_Loopback"
#endif

namespace {

const GUID kSubTypePcm = {WAVE_FORMAT_PCM, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71}};
const GUID kSubTypeIeeeFloat = {WAVE_FORMAT_IEEE_FLOAT, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71}};

struct CaptureFormatInfo {
    WORD format_tag = 0;
    WORD channels = 0;
    WORD container_bits = 0;
    WORD valid_bits = 0;
    DWORD sample_rate = 0;
    WORD sample_bytes = 0;
    WORD block_align = 0;
};

std::string NarrowHex(HRESULT hr) {
    std::ostringstream oss;
    oss << "0x" << std::hex << std::uppercase << static_cast<unsigned long>(hr);
    return oss.str();
}

void LogLine(const char* prefix, const std::string& message) {
    std::fprintf(stderr, "%s|%s\n", prefix, message.c_str());
    std::fflush(stderr);
}

void LogError(const std::string& message, HRESULT hr = S_OK) {
    if (hr == S_OK) {
        LogLine("ERROR", message);
        return;
    }
    LogLine("ERROR", message + " (" + NarrowHex(hr) + ")");
}

class ActivateHandler final : public IActivateAudioInterfaceCompletionHandler {
public:
    ActivateHandler()
        : ref_count_(1), event_(CreateEventW(nullptr, FALSE, FALSE, nullptr)), result_(E_FAIL), client_(nullptr), ftm_(nullptr) {
        CoCreateFreeThreadedMarshaler(
            static_cast<IUnknown*>(static_cast<IActivateAudioInterfaceCompletionHandler*>(this)),
            &ftm_
        );
    }

    ~ActivateHandler() {
        if (ftm_ != nullptr) {
            ftm_->Release();
            ftm_ = nullptr;
        }
        if (client_ != nullptr) {
            client_->Release();
            client_ = nullptr;
        }
        if (event_ != nullptr) {
            CloseHandle(event_);
            event_ = nullptr;
        }
    }

    HRESULT WaitForClient(DWORD timeout_ms, IAudioClient** out_client) {
        if (out_client == nullptr) {
            return E_POINTER;
        }
        *out_client = nullptr;
        if (event_ == nullptr) {
            return E_FAIL;
        }
        DWORD wait_result = WaitForSingleObject(event_, timeout_ms);
        if (wait_result != WAIT_OBJECT_0) {
            return HRESULT_FROM_WIN32(wait_result == WAIT_TIMEOUT ? ERROR_TIMEOUT : GetLastError());
        }
        if (FAILED(result_)) {
            return result_;
        }
        if (client_ == nullptr) {
            return E_FAIL;
        }
        client_->AddRef();
        *out_client = client_;
        return S_OK;
    }

    IFACEMETHODIMP ActivateCompleted(IActivateAudioInterfaceAsyncOperation* operation) override {
        HRESULT activate_hr = E_FAIL;
        IUnknown* activated_interface = nullptr;
        if (operation == nullptr) {
            result_ = E_POINTER;
        } else {
            HRESULT hr = operation->GetActivateResult(&activate_hr, &activated_interface);
            if (FAILED(hr)) {
                result_ = hr;
            } else {
                result_ = activate_hr;
            }
            if (SUCCEEDED(result_) && activated_interface != nullptr) {
                result_ = activated_interface->QueryInterface(__uuidof(IAudioClient), reinterpret_cast<void**>(&client_));
            }
        }
        if (activated_interface != nullptr) {
            activated_interface->Release();
            activated_interface = nullptr;
        }
        if (event_ != nullptr) {
            SetEvent(event_);
        }
        return S_OK;
    }

    IFACEMETHODIMP QueryInterface(REFIID iid, void** object) override {
        if (object == nullptr) {
            return E_POINTER;
        }
        *object = nullptr;
        if (iid == __uuidof(IUnknown) || iid == __uuidof(IActivateAudioInterfaceCompletionHandler) || iid == __uuidof(IAgileObject)) {
            *object = static_cast<IActivateAudioInterfaceCompletionHandler*>(this);
            AddRef();
            return S_OK;
        }
        if (iid == __uuidof(IMarshal) && ftm_ != nullptr) {
            return ftm_->QueryInterface(iid, object);
        }
        return E_NOINTERFACE;
    }

    IFACEMETHODIMP_(ULONG) AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&ref_count_));
    }

    IFACEMETHODIMP_(ULONG) Release() override {
        ULONG value = static_cast<ULONG>(InterlockedDecrement(&ref_count_));
        if (value == 0) {
            delete this;
        }
        return value;
    }

private:
    LONG ref_count_;
    HANDLE event_;
    HRESULT result_;
    IAudioClient* client_;
    IUnknown* ftm_;
};

HRESULT ActivateProcessLoopbackClient(DWORD process_id, IAudioClient** out_client) {
    if (out_client == nullptr) {
        return E_POINTER;
    }
    *out_client = nullptr;

    AUDIOCLIENT_ACTIVATION_PARAMS activation_params = {};
    activation_params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
    activation_params.ProcessLoopbackParams.TargetProcessId = process_id;
    activation_params.ProcessLoopbackParams.ProcessLoopbackMode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;

    PROPVARIANT activate_variant;
    PropVariantInit(&activate_variant);
    activate_variant.vt = VT_BLOB;
    activate_variant.blob.cbSize = static_cast<ULONG>(sizeof(activation_params));
    activate_variant.blob.pBlobData = reinterpret_cast<BYTE*>(&activation_params);

    auto* handler = new ActivateHandler();
    IActivateAudioInterfaceAsyncOperation* async_op = nullptr;
    HRESULT hr = ActivateAudioInterfaceAsync(
        VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        __uuidof(IAudioClient),
        &activate_variant,
        handler,
        &async_op
    );
    if (FAILED(hr)) {
        if (async_op != nullptr) {
            async_op->Release();
            async_op = nullptr;
        }
        handler->Release();
        return hr;
    }

    hr = handler->WaitForClient(10000, out_client);
    if (async_op != nullptr) {
        async_op->Release();
        async_op = nullptr;
    }
    handler->Release();
    return hr;
}

bool DescribeCaptureFormat(const WAVEFORMATEX* format, CaptureFormatInfo* out_info) {
    if (format == nullptr || out_info == nullptr) {
        return false;
    }

    CaptureFormatInfo info = {};
    info.channels = format->nChannels;
    info.container_bits = format->wBitsPerSample;
    info.valid_bits = format->wBitsPerSample;
    info.sample_rate = format->nSamplesPerSec;
    info.block_align = format->nBlockAlign;

    WORD tag = format->wFormatTag;
    if (
        tag == WAVE_FORMAT_EXTENSIBLE
        && format->cbSize >= (sizeof(WAVEFORMATEXTENSIBLE) - sizeof(WAVEFORMATEX))
    ) {
        const auto* extensible = reinterpret_cast<const WAVEFORMATEXTENSIBLE*>(format);
        if (IsEqualGUID(extensible->SubFormat, kSubTypePcm)) {
            tag = WAVE_FORMAT_PCM;
        } else if (IsEqualGUID(extensible->SubFormat, kSubTypeIeeeFloat)) {
            tag = WAVE_FORMAT_IEEE_FLOAT;
        }
        if (extensible->Samples.wValidBitsPerSample > 0) {
            info.valid_bits = extensible->Samples.wValidBitsPerSample;
        }
    }

    info.format_tag = tag;
    info.sample_bytes = static_cast<WORD>((info.container_bits + 7) / 8);

    if (info.channels == 0 || info.sample_bytes == 0 || info.sample_rate == 0 || info.block_align == 0) {
        return false;
    }
    if (info.format_tag == WAVE_FORMAT_PCM) {
        if (!(info.container_bits == 8 || info.container_bits == 16 || info.container_bits == 24 || info.container_bits == 32)) {
            return false;
        }
        *out_info = info;
        return true;
    }
    if (info.format_tag == WAVE_FORMAT_IEEE_FLOAT) {
        if (!(info.container_bits == 32 || info.container_bits == 64)) {
            return false;
        }
        *out_info = info;
        return true;
    }
    return false;
}

double ClampUnit(double value) {
    if (value > 1.0) {
        return 1.0;
    }
    if (value < -1.0) {
        return -1.0;
    }
    return value;
}

double ReadNormalizedSample(const BYTE* sample, const CaptureFormatInfo& info) {
    if (sample == nullptr) {
        return 0.0;
    }

    if (info.format_tag == WAVE_FORMAT_IEEE_FLOAT) {
        if (info.sample_bytes == 4) {
            float value = 0.0f;
            std::memcpy(&value, sample, sizeof(value));
            return ClampUnit(static_cast<double>(value));
        }
        if (info.sample_bytes == 8) {
            double value = 0.0;
            std::memcpy(&value, sample, sizeof(value));
            return ClampUnit(value);
        }
        return 0.0;
    }

    if (info.sample_bytes == 1) {
        const int value = static_cast<int>(sample[0]) - 128;
        return ClampUnit(static_cast<double>(value) / 128.0);
    }
    if (info.sample_bytes == 2) {
        int16_t value = 0;
        std::memcpy(&value, sample, sizeof(value));
        return ClampUnit(static_cast<double>(value) / 32768.0);
    }
    if (info.sample_bytes == 3) {
        int32_t value = (static_cast<int32_t>(sample[0]))
            | (static_cast<int32_t>(sample[1]) << 8)
            | (static_cast<int32_t>(sample[2]) << 16);
        if ((value & 0x00800000) != 0) {
            value |= ~0x00FFFFFF;
        }
        return ClampUnit(static_cast<double>(value) / 8388608.0);
    }
    if (info.sample_bytes == 4) {
        int32_t value = 0;
        std::memcpy(&value, sample, sizeof(value));
        return ClampUnit(static_cast<double>(value) / 2147483648.0);
    }
    return 0.0;
}

int16_t FloatToPcm16(double value) {
    const double clamped = ClampUnit(value);
    const long sample = std::lround(clamped * 32767.0);
    if (sample > 32767) {
        return 32767;
    }
    if (sample < -32768) {
        return -32768;
    }
    return static_cast<int16_t>(sample);
}

void ConvertPacketToStereoPcm16(
    const BYTE* source,
    UINT32 frames_available,
    const CaptureFormatInfo& info,
    std::vector<BYTE>* out_bytes
) {
    if (out_bytes == nullptr) {
        return;
    }
    const size_t frame_count = static_cast<size_t>(frames_available);
    out_bytes->assign(frame_count * 4, 0);
    if (source == nullptr || frame_count == 0) {
        return;
    }

    auto* dest = reinterpret_cast<int16_t*>(out_bytes->data());
    const size_t sample_stride = static_cast<size_t>(info.sample_bytes);
    const size_t frame_stride = static_cast<size_t>(info.block_align);

    for (size_t frame_idx = 0; frame_idx < frame_count; ++frame_idx) {
        const BYTE* frame_ptr = source + (frame_idx * frame_stride);
        double left = ReadNormalizedSample(frame_ptr, info);
        double right = left;
        if (info.channels >= 2) {
            right = ReadNormalizedSample(frame_ptr + sample_stride, info);
        }
        dest[(frame_idx * 2) + 0] = FloatToPcm16(left);
        dest[(frame_idx * 2) + 1] = FloatToPcm16(right);
    }
}

HRESULT InitializeClientFormat(
    IAudioClient* audio_client,
    std::vector<BYTE>* out_format_blob,
    bool* out_use_event_callback
) {
    if (audio_client == nullptr || out_format_blob == nullptr || out_use_event_callback == nullptr) {
        return E_POINTER;
    }

    out_format_blob->clear();
    *out_use_event_callback = false;

    WAVEFORMATEX* mix_format = nullptr;
    HRESULT hr = audio_client->GetMixFormat(&mix_format);
    if (SUCCEEDED(hr) && mix_format != nullptr) {
        const size_t format_size = sizeof(WAVEFORMATEX) + static_cast<size_t>(mix_format->cbSize);
        out_format_blob->resize(format_size);
        std::memcpy(out_format_blob->data(), mix_format, format_size);

        const DWORD stream_flags[] = {
            AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
            AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
        };
        for (DWORD flags : stream_flags) {
            hr = audio_client->Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                flags,
                0,
                0,
                reinterpret_cast<WAVEFORMATEX*>(out_format_blob->data()),
                nullptr
            );
            if (SUCCEEDED(hr)) {
                *out_use_event_callback = (flags & AUDCLNT_STREAMFLAGS_EVENTCALLBACK) != 0;
                break;
            }
        }

        CoTaskMemFree(mix_format);
        if (SUCCEEDED(hr)) {
            return hr;
        }
        out_format_blob->clear();
    } else if (mix_format != nullptr) {
        CoTaskMemFree(mix_format);
    }

    const DWORD sample_rates[] = {44100, 48000};
    for (DWORD sample_rate : sample_rates) {
        WAVEFORMATEX format = {};
        format.wFormatTag = WAVE_FORMAT_PCM;
        format.nChannels = 2;
        format.nSamplesPerSec = sample_rate;
        format.wBitsPerSample = 16;
        format.nBlockAlign = static_cast<WORD>(format.nChannels * (format.wBitsPerSample / 8));
        format.nAvgBytesPerSec = format.nSamplesPerSec * format.nBlockAlign;
        format.cbSize = 0;

        const struct CaptureCandidate {
            DWORD flags;
            WORD format_tag;
            WORD bits_per_sample;
        } candidates[] = {
            {AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY, WAVE_FORMAT_IEEE_FLOAT, 32},
            {AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY, WAVE_FORMAT_IEEE_FLOAT, 32},
            {AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY, WAVE_FORMAT_PCM, 16},
            {AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY, WAVE_FORMAT_PCM, 16},
        };
        for (const auto& candidate : candidates) {
            format.wFormatTag = candidate.format_tag;
            format.wBitsPerSample = candidate.bits_per_sample;
            format.nBlockAlign = static_cast<WORD>(format.nChannels * (format.wBitsPerSample / 8));
            format.nAvgBytesPerSec = format.nSamplesPerSec * format.nBlockAlign;
            hr = audio_client->Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                candidate.flags,
                0,
                0,
                &format,
                nullptr
            );
            if (SUCCEEDED(hr)) {
                out_format_blob->resize(sizeof(WAVEFORMATEX));
                std::memcpy(out_format_blob->data(), &format, sizeof(WAVEFORMATEX));
                *out_use_event_callback = (candidate.flags & AUDCLNT_STREAMFLAGS_EVENTCALLBACK) != 0;
                return hr;
            }
        }
    }

    return hr;
}

bool WriteAll(const BYTE* data, size_t bytes_to_write) {
    if (data == nullptr || bytes_to_write == 0) {
        return true;
    }
    size_t written_total = 0;
    while (written_total < bytes_to_write) {
        size_t wrote = std::fwrite(data + written_total, 1, bytes_to_write - written_total, stdout);
        if (wrote == 0) {
            return false;
        }
        written_total += wrote;
    }
    std::fflush(stdout);
    return true;
}

int RunCapture(DWORD process_id) {
    HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    const bool coinit_ok = SUCCEEDED(hr);
    if (FAILED(hr) && hr != RPC_E_CHANGED_MODE) {
        LogError("CoInitializeEx failed", hr);
        return 1;
    }

    IAudioClient* audio_client = nullptr;
    IAudioCaptureClient* capture_client = nullptr;
    HANDLE avrt_handle = nullptr;
    HANDLE samples_ready_event = nullptr;
    DWORD task_index = 0;
    int exit_code = 0;
    std::vector<BYTE> silent_buffer;
    std::vector<BYTE> capture_format_blob;
    CaptureFormatInfo capture_format = {};
    std::vector<BYTE> converted_packet;
    bool use_event_callback = false;

    if (_setmode(_fileno(stdout), _O_BINARY) < 0) {
        LogError("Could not switch stdout to binary mode");
        exit_code = 1;
        goto cleanup;
    }
    std::setvbuf(stdout, nullptr, _IONBF, 0);

    hr = ActivateProcessLoopbackClient(process_id, &audio_client);
    if (FAILED(hr) || audio_client == nullptr) {
        LogError("Could not activate process loopback client", hr);
        exit_code = 1;
        goto cleanup;
    }

    hr = InitializeClientFormat(audio_client, &capture_format_blob, &use_event_callback);
    if (FAILED(hr)) {
        LogError("Could not initialize app capture stream format", hr);
        exit_code = 1;
        goto cleanup;
    }

    if (capture_format_blob.empty() || !DescribeCaptureFormat(reinterpret_cast<const WAVEFORMATEX*>(capture_format_blob.data()), &capture_format)) {
        LogError("Unsupported app capture mix format");
        exit_code = 1;
        goto cleanup;
    }

    if (use_event_callback) {
        samples_ready_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        if (samples_ready_event == nullptr) {
            LogError("Could not create app capture event handle", HRESULT_FROM_WIN32(GetLastError()));
            exit_code = 1;
            goto cleanup;
        }

        hr = audio_client->SetEventHandle(samples_ready_event);
        if (FAILED(hr)) {
            LogError("Could not attach app capture event handle", hr);
            exit_code = 1;
            goto cleanup;
        }
    }

    hr = audio_client->GetService(__uuidof(IAudioCaptureClient), reinterpret_cast<void**>(&capture_client));
    if (FAILED(hr) || capture_client == nullptr) {
        LogError("Could not open process capture service", hr);
        exit_code = 1;
        goto cleanup;
    }

    std::fprintf(stderr, "FORMAT|%lu|%u|%u\n",
        static_cast<unsigned long>(capture_format.sample_rate),
        2U,
        16U
    );
    std::fflush(stderr);

    avrt_handle = AvSetMmThreadCharacteristicsW(L"Audio", &task_index);

    hr = audio_client->Start();
    if (FAILED(hr)) {
        LogError("Could not start process capture stream", hr);
        exit_code = 1;
        goto cleanup;
    }

    while (true) {
        if (use_event_callback) {
            DWORD wait_result = WaitForSingleObject(samples_ready_event, 2000);
            if (wait_result == WAIT_TIMEOUT) {
                continue;
            }
            if (wait_result != WAIT_OBJECT_0) {
                LogError("App capture event wait failed", HRESULT_FROM_WIN32(GetLastError()));
                exit_code = 1;
                break;
            }
        } else {
            Sleep(10);
        }

        UINT32 packet_frames = 0;
        hr = capture_client->GetNextPacketSize(&packet_frames);
        if (FAILED(hr)) {
            LogError("Could not query next capture packet", hr);
            exit_code = 1;
            break;
        }

        if (packet_frames == 0) {
            continue;
        }

        while (packet_frames > 0) {
            BYTE* data = nullptr;
            UINT32 frames_available = 0;
            DWORD flags = 0;
            hr = capture_client->GetBuffer(
                &data,
                &frames_available,
                &flags,
                nullptr,
                nullptr
            );
            if (FAILED(hr)) {
                LogError("Could not read capture buffer", hr);
                exit_code = 1;
                goto capture_done;
            }

            const size_t bytes_available = static_cast<size_t>(frames_available) * 4;
            bool ok = true;
            if ((flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0U) {
                if (silent_buffer.size() < bytes_available) {
                    silent_buffer.assign(bytes_available, 0);
                }
                ok = WriteAll(silent_buffer.data(), bytes_available);
            } else {
                ConvertPacketToStereoPcm16(data, frames_available, capture_format, &converted_packet);
                ok = WriteAll(converted_packet.data(), converted_packet.size());
            }

            hr = capture_client->ReleaseBuffer(frames_available);
            if (FAILED(hr)) {
                LogError("Could not release capture buffer", hr);
                exit_code = 1;
                goto capture_done;
            }

            if (!ok) {
                goto capture_done;
            }

            hr = capture_client->GetNextPacketSize(&packet_frames);
            if (FAILED(hr)) {
                LogError("Could not query following capture packet", hr);
                exit_code = 1;
                goto capture_done;
            }
        }
    }

capture_done:
    audio_client->Stop();

cleanup:
    if (capture_client != nullptr) {
        capture_client->Release();
        capture_client = nullptr;
    }
    if (audio_client != nullptr) {
        audio_client->Release();
        audio_client = nullptr;
    }
    if (avrt_handle != nullptr) {
        AvRevertMmThreadCharacteristics(avrt_handle);
        avrt_handle = nullptr;
    }
    if (samples_ready_event != nullptr) {
        CloseHandle(samples_ready_event);
        samples_ready_event = nullptr;
    }
    if (coinit_ok) {
        CoUninitialize();
    }
    return exit_code;
}

bool ParseCapturePid(int argc, wchar_t** argv, DWORD* out_pid) {
    if (out_pid == nullptr) {
        return false;
    }
    *out_pid = 0;
    for (int idx = 1; idx < argc; ++idx) {
        if (std::wcscmp(argv[idx], L"--capture") == 0 || std::wcscmp(argv[idx], L"--pid") == 0) {
            if (idx + 1 >= argc) {
                return false;
            }
            *out_pid = static_cast<DWORD>(std::wcstoul(argv[idx + 1], nullptr, 10));
            return *out_pid > 0;
        }
    }
    return false;
}

void PrintUsage() {
    LogLine("ERROR", "Usage: mixsplitr_process_loopback.exe --capture <pid>");
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    DWORD process_id = 0;
    if (!ParseCapturePid(argc, argv, &process_id)) {
        PrintUsage();
        return 1;
    }
    return RunCapture(process_id);
}
