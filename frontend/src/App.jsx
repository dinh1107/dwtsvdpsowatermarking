import React, { useState } from 'react';
import axios from 'axios';
import './App.css';

function App() {
  const [activeTab, setActiveTab] = useState('embed');

  // ================= 1. KHAI BÁO TOÀN BỘ STATE =================
  const [isLoading, setIsLoading] = useState(false);
  const [hostFile, setHostFile] = useState(null);
  const [logoFile, setLogoFile] = useState(null);
  const [suspectFile, setSuspectFile] = useState(null);
  const [alpha, setAlpha] = useState(0.1);
  const [usePSO, setUsePSO] = useState(true);

  // State: Tab Nhúng Ảnh
  const [embedResultImage, setEmbedResultImage] = useState(null);
  const [embeddedBlob, setEmbeddedBlob] = useState(null);
  const [copyrightFileUrl, setCopyrightFileUrl] = useState(null);
  
  // State: Báo cáo Tấn công
  const [attackType, setAttackType] = useState('jpeg');
  const [attackIntensity, setAttackIntensity] = useState(50);
  const [attackData, setAttackData] = useState(null);

  // State: Tab Trích xuất
  const [extractResultImage, setExtractResultImage] = useState(null);
  const [ncScore, setNcScore] = useState(null);

  // State: Tab Video
  const [videoFile, setVideoFile] = useState(null);
  const [videoMode, setVideoMode] = useState('hidden');
  const [videoPosition, setVideoPosition] = useState('bottom-right');
  const [videoResultUrl, setVideoResultUrl] = useState(null);

  const attackConfigs = {
    jpeg: { label: "Nén JPEG", min: 10, max: 100, step: 1, default: 50, unit: "% chất lượng" },
    noise: { label: "Nhiễu hạt Gaussian", min: 0.01, max: 0.2, step: 0.01, default: 0.05, unit: "phương sai" },
    crop: { label: "Cắt xén viền", min: 0.05, max: 0.4, step: 0.05, default: 0.2, unit: "% diện tích" },
    blur: { label: "Làm mờ (Blur)", min: 3, max: 25, step: 2, default: 7, unit: "ksize" }
  };

  // ================= 2. CÁC HÀM XỬ LÝ LOGIC API =================

  // Hàm nhúng Ảnh
  const handleEmbed = async (e) => {
    e.preventDefault();
    if (!hostFile || !logoFile) return alert("⚠️ Vui lòng tải đủ Ảnh gốc và Logo!");
    setIsLoading(true); setCopyrightFileUrl(null); setEmbedResultImage(null); setEmbeddedBlob(null); setAttackData(null);

    const formData = new FormData();
    formData.append("host_file", hostFile);
    formData.append("logo_file", logoFile);
    formData.append("alpha", alpha);
    formData.append("use_pso", usePSO);

    try {
      const response = await axios.post("http://127.0.0.1:8000/api/nhung-thuy-van", formData);
      const data = response.data;
      
      if (usePSO) setAlpha(data.optimized_alpha);

      // Chuyển Base64 thành Blob để dùng cho hàm tấn công tiếp theo
      const fetchRes = await fetch(data.stego_image);
      const blobData = await fetchRes.blob();
      setEmbeddedBlob(blobData);
      setEmbedResultImage(data.stego_image);

      const copyrightInfo = {
        title: "HỒ SƠ BẢN QUYỀN", 
        author: "TRỊNH VĂN ĐỊNH",
        timestamp: new Date().toLocaleString('vi-VN'),
        algorithms_used: ["DWT (Haar)", "SVD", usePSO ? "PSO" : "Manual Alpha"],
        image_data: { original_filename: hostFile.name, logo_filename: logoFile.name },
        security_keys: { alpha_embedded: data.optimized_alpha },
        warning: "Vui lòng giữ kín file này để phục vụ việc trích xuất."
      };
      setCopyrightFileUrl(URL.createObjectURL(new Blob([JSON.stringify(copyrightInfo, null, 2)], { type: 'application/json' })));
      
    } catch (error) { alert("❌ Lỗi Server Python!"); } finally { setIsLoading(false); }
  };

  // Hàm Tấn công ảnh (Lấy blob trên RAM)
  const handleAttack = async (e) => {
    e.preventDefault();
    if (!embeddedBlob) return alert("⚠️ Chưa có ảnh nhúng để kiểm thử!");
    setIsLoading(true); setAttackData(null);

    const formData = new FormData();
    formData.append("watermarked_file", embeddedBlob, "stego.png"); 
    formData.append("host_file", hostFile);
    formData.append("logo_file", logoFile);
    formData.append("alpha", alpha); 
    formData.append("attack_type", attackType);
    formData.append("intensity", attackIntensity);

    try {
      const response = await axios.post("http://127.0.0.1:8000/api/tan-cong", formData);
      setAttackData(response.data); 
    } catch (error) { alert("❌ Lỗi khi giả lập tấn công!"); } finally { setIsLoading(false); }
  };

  // Hàm Trích xuất ảnh
  const handleExtract = async (e) => {
    e.preventDefault();
    if (!suspectFile || !hostFile || !logoFile) return alert("⚠️ Cần đủ 3 file (Nghi ngờ, Gốc, Logo)!");
    setIsLoading(true); setExtractResultImage(null); setNcScore(null);

    const formData = new FormData();
    formData.append("watermarked_file", suspectFile);
    formData.append("host_file", hostFile);
    formData.append("logo_file", logoFile);
    formData.append("alpha", alpha);

    try {
      const response = await axios.post("http://127.0.0.1:8000/api/trich-xuat", formData);
      setExtractResultImage(response.data.extracted_logo);
      setNcScore(response.data.nc_score);
    } catch (error) {
      if (error.response?.status === 403) alert(`🚨 TỪ CHỐI TRUY CẬP 🚨\n\n${error.response.data.detail}`);
      else if (error.response?.status === 412) alert(`🚨 FILE NGHI NGỜ KHÔNG AN TOÀN 🚨\n\n${error.response.data.detail.msg}`);
      else alert("❌ Lỗi kết nối Server!");
    } finally { setIsLoading(false); }
  };

  // Hàm Nhúng Video
  const handleVideoProcess = async (e) => {
    e.preventDefault();
    if (!videoFile || !logoFile) return alert("⚠️ Vui lòng tải đủ Video gốc và Logo!");
    setIsLoading(true); setVideoResultUrl(null);

    const formData = new FormData();
    formData.append("video_file", videoFile);
    formData.append("logo_file", logoFile);
    formData.append("mode", videoMode);
    formData.append("position", videoPosition);
    formData.append("alpha", alpha);

    try {
      const response = await axios.post("http://127.0.0.1:8000/api/nhung-video", formData, { responseType: 'blob' });
      setVideoResultUrl(URL.createObjectURL(response.data));
    } catch (error) { alert("❌ Lỗi xử lý Video! Thời gian xử lý có thể lâu gây Timeout."); } finally { setIsLoading(false); }
  };

  // ================= 3. RENDER UI =================
  return (
    <div className="app-container">
      <header className="app-header">
        <h1>🛡️ Hệ thống Bảo vệ Bản quyền Số</h1>
        <p>Kiến trúc Thuật toán DWT-SVD + Trí tuệ Bầy đàn PSO</p>
      </header>

      {/* MENU 3 TABS */}
      <div style={{ display: 'flex', justifyContent: 'center', gap: '15px', marginBottom: '30px', flexWrap: 'wrap' }}>
        <button onClick={() => setActiveTab('embed')} style={{ backgroundColor: activeTab === 'embed' ? '#3b82f6' : '#e5e7eb', color: activeTab === 'embed' ? 'white' : '#4b5563', padding: '10px 20px', borderRadius: '8px', border: 'none', fontWeight: 'bold', cursor: 'pointer', transition: '0.3s' }}>🔒 Nhúng Ảnh</button>
        <button onClick={() => setActiveTab('extract')} style={{ backgroundColor: activeTab === 'extract' ? '#10b981' : '#e5e7eb', color: activeTab === 'extract' ? 'white' : '#4b5563', padding: '10px 20px', borderRadius: '8px', border: 'none', fontWeight: 'bold', cursor: 'pointer', transition: '0.3s' }}>🔍 Xác Minh Ảnh</button>
        <button onClick={() => setActiveTab('video')} style={{ backgroundColor: activeTab === 'video' ? '#8b5cf6' : '#e5e7eb', color: activeTab === 'video' ? 'white' : '#4b5563', padding: '10px 20px', borderRadius: '8px', border: 'none', fontWeight: 'bold', cursor: 'pointer', transition: '0.3s' }}>🎥 Xử lý Video</button>
      </div>

      <main className="main-content">
        {/* ================= KHỐI ĐIỀU KHIỂN ================= */}
        <div className="control-panel">
          <h2 className="panel-title">⚙️ Bảng Điều Khiển</h2>
          
          {/* FILE UPLOAD (Thay đổi theo Tab) */}
          {activeTab !== 'video' ? (
            <div className="form-group">
              <label>🖼️ Ảnh Nghệ Thuật Gốc</label>
              <input type="file" accept="image/*" onChange={(e) => setHostFile(e.target.files[0])} />
            </div>
          ) : (
            <div className="form-group">
              <label>🎞️ Video Gốc (.mp4, .avi)</label>
              <input type="file" accept="video/*" onChange={(e) => setVideoFile(e.target.files[0])} />
            </div>
          )}

          <div className="form-group">
            <label>©️ Logo Bản Quyền</label>
            <input type="file" accept="image/*" onChange={(e) => setLogoFile(e.target.files[0])} />
          </div>
          
          {activeTab === 'extract' && (
            <div className="form-group" style={{border: '1px dashed #e74c3c', padding: '10px', borderRadius: '8px'}}>
              <label style={{color: '#e74c3c'}}>🚨 Ảnh bị nghi ngờ ăn cắp</label>
              <input type="file" accept="image/*" onChange={(e) => setSuspectFile(e.target.files[0])} />
            </div>
          )}

          {/* CẤU HÌNH NHÚNG ẢNH/TRÍCH XUẤT */}
          {activeTab !== 'video' && (
            <div className="form-group">
              <label>Hệ số năng lượng (Alpha) <span className="value-badge">{alpha}</span></label>
              <input type="number" step="0.0001" value={alpha} onChange={(e) => setAlpha(e.target.value)} style={{width: '100%', padding: '8px', borderRadius: '6px', border: '1px solid #ccc'}}/>
            </div>
          )}

          {activeTab === 'embed' && (
            <>
              <div className="form-group">
                <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', color: '#4338ca', fontWeight: 'bold' }}>
                  <input type="checkbox" checked={usePSO} onChange={(e) => setUsePSO(e.target.checked)} style={{ marginRight: '10px', width: '18px', height: '18px' }}/>
                  🤖 Bật PSO tự động tìm Alpha
                </label>
              </div>
              <button onClick={handleEmbed} className="btn-primary" disabled={isLoading}>{isLoading ? "⏳ Đang chạy thuật toán..." : "🚀 Bắt Đầu Đóng Dấu"}</button>
              
              {embeddedBlob && (
                 <div style={{marginTop: '30px', backgroundColor: '#fffbeb', padding: '15px', borderRadius: '8px', border: '1px solid #fcd34d'}}>
                   <h3 style={{margin: '0 0 10px 0', color: '#d97706'}}>⚔️ Thử nghiệm sức chịu đựng</h3>
                   <select value={attackType} onChange={(e) => { setAttackType(e.target.value); setAttackIntensity(attackConfigs[e.target.value].default); }} style={{width: '100%', padding: '8px', margin: '5px 0 15px 0', borderRadius: '6px'}}>
                     <option value="jpeg">🗜️ Nén dung lượng (JPEG)</option>
                     <option value="noise">🌨️ Nhiễu hạt (Gaussian Noise)</option>
                     <option value="crop">✂️ Cắt xén viền (Crop)</option>
                     <option value="blur">🌫️ Làm mờ ảnh (Blur)</option>
                   </select>
                   <label>Cường độ: <span style={{fontWeight: 'bold', color: '#e74c3c'}}>{attackIntensity}</span> <small>{attackConfigs[attackType].unit}</small></label>
                   <input type="range" min={attackConfigs[attackType].min} max={attackConfigs[attackType].max} step={attackConfigs[attackType].step} value={attackIntensity} onChange={(e) => setAttackIntensity(e.target.value)} style={{width: '100%', marginBottom: '15px'}} />
                   <button onClick={handleAttack} className="btn-primary" style={{backgroundColor: '#f59e0b', width: '100%'}} disabled={isLoading}>{isLoading ? "⏳ Đang tàn phá ảnh..." : "🔥 Giả Lập Tấn Công"}</button>
                 </div>
              )}
            </>
          )}

          {activeTab === 'extract' && <button onClick={handleExtract} className="btn-primary" style={{backgroundColor: '#10b981'}} disabled={isLoading}>{isLoading ? "⏳ Đang phân tích ma trận..." : "🔍 Quét và Giải mã"}</button>}

          {/* CẤU HÌNH VIDEO */}
          {activeTab === 'video' && (
             <>
               <div className="form-group">
                 <label>Chế độ nhúng:</label>
                 <div style={{ display: 'flex', gap: '15px', marginTop: '10px' }}>
                   <label><input type="radio" name="mode" value="hidden" checked={videoMode === 'hidden'} onChange={(e) => setVideoMode(e.target.value)} /> 🕵️ Nhúng Ẩn (DWT-SVD)</label>
                   <label><input type="radio" name="mode" value="visible" checked={videoMode === 'visible'} onChange={(e) => setVideoMode(e.target.value)} /> 👁️ Nhúng Hiện</label>
                 </div>
               </div>

               {videoMode === 'visible' ? (
                 <div className="form-group">
                   <label>Vị trí Logo:</label>
                   <select value={videoPosition} onChange={(e) => setVideoPosition(e.target.value)} style={{ width: '100%', padding: '10px', borderRadius: '8px' }}>
                     <option value="top-left">↖️ Góc trên trái</option>
                     <option value="top-right">↗️ Góc trên phải</option>
                     <option value="center">⏺️ Chính giữa</option>
                     <option value="bottom-left">↙️ Góc dưới trái</option>
                     <option value="bottom-right">↘️ Góc dưới phải</option>
                   </select>
                 </div>
               ) : (
                  <div className="form-group">
                    <label>Hệ số Alpha <span className="value-badge">{alpha}</span></label>
                    <input type="number" step="0.0001" value={alpha} onChange={(e) => setAlpha(e.target.value)} style={{width: '100%', padding: '8px', borderRadius: '6px', border: '1px solid #ccc'}}/>
                  </div>
               )}
               <button onClick={handleVideoProcess} className="btn-primary" style={{ backgroundColor: '#8b5cf6' }} disabled={isLoading}>{isLoading ? "⏳ Đang Render Video..." : "🎬 Bắt Đầu Xuất Video"}</button>
             </>
          )}
        </div>

        {/* ================= KHỐI HIỂN THỊ KẾT QUẢ ================= */}
        <div className="result-panel">
          {activeTab === 'embed' && (
            <>
              <h2 className="panel-title">✨ Kết quả Cấp bản quyền (Ảnh)</h2>
              {embedResultImage ? (
                <div style={{ textAlign: 'center', marginBottom: '30px' }}>
                  <img src={embedResultImage} alt="Kết quả" className="result-image" style={{maxHeight: '300px'}} />
                  <div style={{ display: 'flex', gap: '10px', marginTop: '15px' }}>
                    <a href={embedResultImage} download="protected_image.png" style={{flex: 1, textDecoration: 'none'}}>
                      <button className="btn-download" style={{width: '100%'}}>⬇️ Tải Ảnh Mới</button>
                    </a>
                    {copyrightFileUrl && (
                      <a href={copyrightFileUrl} download="HoSo_BanQuyen.json" style={{flex: 1, textDecoration: 'none'}}>
                        <button className="btn-download" style={{width: '100%', backgroundColor: '#f59e0b'}}>📄 Tải Hồ Sơ (.json)</button>
                      </a>
                    )}
                  </div>
                </div>
              ) : <div className="result-placeholder" style={{minHeight: '150px'}}><p>Chưa có dữ liệu.</p></div>}

              {attackData && (
                <div style={{ borderTop: '2px dashed #ccc', paddingTop: '20px' }}>
                  <h2 className="panel-title" style={{color: '#d97706'}}>📊 Báo cáo Sức chịu đựng</h2>
                  <div style={{padding: '10px', borderRadius: '8px', backgroundColor: attackData.nc_score > 0.8 ? '#d1fae5' : '#fee2e2', textAlign: 'center', marginBottom: '15px', border: '2px solid', borderColor: attackData.nc_score > 0.8 ? '#10b981' : '#ef4444'}}>
                      <h3 style={{margin: 0, color: attackData.nc_score > 0.8 ? '#065f46' : '#991b1b'}}>Độ nguyên vẹn NC: {(attackData.nc_score * 100).toFixed(2)}%</h3>
                  </div>
                  <div style={{ display: 'flex', gap: '10px' }}>
                    <div style={{ flex: 1, textAlign: 'center' }}>
                      <p style={{fontWeight: 'bold', color: '#e74c3c', margin: '0 0 5px 0'}}>Ảnh bị Tàn phá</p>
                      <img src={attackData.attacked_image} alt="Attacked" style={{width: '100%', borderRadius: '8px', border: '1px solid #ccc'}} />
                    </div>
                    <div style={{ flex: 1, textAlign: 'center' }}>
                      <p style={{fontWeight: 'bold', color: '#10b981', margin: '0 0 5px 0'}}>Logo Cứu được</p>
                      <img src={attackData.extracted_logo} alt="Extracted" style={{maxWidth: '100%', borderRadius: '8px', border: '1px solid #ccc', backgroundColor: '#000'}} />
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {activeTab === 'extract' && (
            <>
              <h2 className="panel-title">🛡️ Bằng chứng (Phục vụ tranh chấp)</h2>
              {extractResultImage ? (
                <div style={{ textAlign: 'center' }}>
                  <img src={extractResultImage} alt="Logo" className="result-image" style={{maxWidth: '256px', backgroundColor: '#000', padding: '10px'}} />
                  {ncScore && (
                    <div style={{marginTop: '20px', padding: '15px', borderRadius: '8px', backgroundColor: '#d1fae5', border: '1px solid #34d399'}}>
                      <h3 style={{margin: 0, color: '#065f46'}}>Độ khớp NC: {(ncScore * 100).toFixed(2)}%</h3>
                      <p style={{margin: '5px 0 0 0', color: '#047857'}}>Bằng chứng hợp lệ, khớp với hồ sơ JSON!</p>
                    </div>
                  )}
                </div>
              ) : <div className="result-placeholder"><p>Hệ thống đang chờ bằng chứng.</p></div>}
            </>
          )}

          {activeTab === 'video' && (
            <>
              <h2 className="panel-title">📽️ Kết quả Render Video</h2>
              {videoResultUrl ? (
                <div style={{ textAlign: 'center' }}>
                  <video 
                    controls 
                    autoPlay 
                    style={{ width: '100%', borderRadius: '12px', marginBottom: '15px', border: '1px solid #ccc', backgroundColor: '#000' }}
                  >
                    <source src={videoResultUrl} type="video/webm" />
                  </video>
                  <a href={videoResultUrl} download={`watermarked_video_${videoMode}.mp4`} style={{ textDecoration: 'none' }}>
                    <button className="btn-download" style={{ width: '100%', backgroundColor: '#8b5cf6' }}>⬇️ Tải Video Về máy</button>
                  </a>
                </div>
              ) : <div className="result-placeholder"><p>Video kết quả sẽ hiển thị ở đây.</p></div>}
            </>
          )}
        </div>
      </main>
    </div>
  );
}

export default App;