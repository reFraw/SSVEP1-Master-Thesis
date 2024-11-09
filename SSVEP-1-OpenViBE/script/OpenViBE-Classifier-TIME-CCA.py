import socket
import numpy as np
import scipy as sp
import matplotlib.pyplot as plt
from sklearn.cross_decomposition import CCA
import spkit

class SsvepDetector():
    
    def __init__(self):
        self.notchFreq = 50
        
    def __applyNotchFilter(self, signal, samplingRate):
        
        b, a = sp.signal.iirnotch(self.notchFreq, 30, samplingRate)
        notched = sp.signal.lfilter(b, a, signal)
        
        return notched
        
    def __applyBandpassFilter(self,signal, samplingRate, lowcut, highcut, order=4):
        
        nyq = 0.5 * samplingRate
        low = lowcut / nyq
        high = highcut / nyq

        b, a = sp.signal.butter(order, [low, high], btype="band")
        
        filtered = sp.signal.lfilter(b, a, signal)
        
        return filtered
    
    def __getPSD(self, signal, samplingRate):
        
        nSample = len(signal)
        dt = 1/samplingRate
        
        fftSignal = np.fft.fft(signal)
        
        fftFreq = np.fft.fftfreq(nSample, d=dt)
        fftFreq = fftFreq[:nSample // 2]
        
        psd = (np.abs(fftSignal) ** 2) / (nSample * samplingRate)
        psd = psd[:nSample // 2]
        
        return fftFreq, psd
    
    def _calculate_cca_score(self, signal, sampling_rate, target_frequencies, num_harmonics=1):
        """
        Calcola il punteggio CCA per ciascuna frequenza target e restituisce quella con la correlazione maggiore.
        
        Parametri:
            signal (np.array): Segnale di input come array numpy.
            sampling_rate (int): Frequenza di campionamento del segnale.
            target_frequencies (list): Lista delle frequenze target da valutare.
            num_harmonics (int): Numero di armoniche da considerare per ciascuna frequenza target.
        
        Ritorna:
            best_freq (float): Frequenza target con la correlazione maggiore.
            best_score (float): Valore di correlazione massimo.
        """
        # Lunghezza del segnale
        n_samples = len(signal)
        # Array temporale per generare i riferimenti
        time = np.arange(n_samples) / sampling_rate
        
        # Inizializza variabili per tracciare il miglior punteggio e la frequenza
        best_score = 0
        best_freq = 0
        
        # Itera su ciascuna frequenza target
        for freq in target_frequencies:
            # Genera i riferimenti considerando il numero di armoniche
            reference_signals = []
            for h in range(1, num_harmonics + 1):
                reference_signals.append(np.sin(2 * np.pi * freq * h * time))
                reference_signals.append(np.cos(2 * np.pi * freq * h * time))
            reference_signals = np.array(reference_signals).T  # Trasposta per avere (n_samples, n_references)
            
            # Applica CCA
            cca = CCA(n_components=1)
            cca.fit(signal.reshape(-1, 1), reference_signals)
            X_c, Y_c = cca.transform(signal.reshape(-1, 1), reference_signals)
            
            # Calcola il punteggio come correlazione
            score = np.corrcoef(X_c.T, Y_c.T)[0, 1]
            
            # Aggiorna se il punteggio è il migliore trovato
            if score > best_score:
                best_score = score
                best_freq = freq
        
        return best_freq, best_score
    
    def predict(self, OzSignal, FzSignal, samplingRate, targets):
        """
        Determina la presenza di picchi SSVEP.
        
        Parameters:
        - OzSignal: array numpy.
        - FzSignal: array numpy.
        - SamplingRate: Frequenza di campionamento del segnale.
        - targets: lista con le frequenze di interesse.
        """
        
        freqTolerance = 0.5
        
        FzNotched = self.__applyNotchFilter(FzSignal, samplingRate)
        FzFiltered = self.__applyBandpassFilter(FzNotched, samplingRate, 10, 30, 5)
        
        OzNotched = self.__applyNotchFilter(OzSignal, samplingRate)
        OzFiltered = self.__applyBandpassFilter(OzNotched, samplingRate, 10, 30, 5)
        
        differenceSig = OzFiltered - FzFiltered
        
        f, diffPSD = self.__getPSD(differenceSig, samplingRate)
        
        peakIdx = np.argsort(diffPSD)[-2:]
        
        firstPeakFreq = f[peakIdx[-1]]
        secondPeakFreq = f[peakIdx[-2]]
        
        for target in targets:
            delta = np.abs(firstPeakFreq - target)
            if delta < 1:
                power = diffPSD[peakIdx[-1]]
                if power > max(1.5, 6 - 0.12 * target):
                    checkCCA = self._calculate_cca_score(differenceSig, samplingRate, targets, 1)
                    if checkCCA[0] == target:
                        return (target, firstPeakFreq)
                    else:
                        if secondPeakFreq <= target + freqTolerance and secondPeakFreq >= target - freqTolerance:
                            power = diffPSD[peakIdx[-2]]
                            if power > max(1.5, 6 - 0.22 * target):
                                checkCCA = self._calculate_cca_score(differenceSig, samplingRate, targets, 1)
                                if checkCCA[0] == target:
                                    return (target, secondPeakFreq)
                                else:
                                    return None
                            else:
                                return None
                else:
                    if secondPeakFreq <= target + freqTolerance and secondPeakFreq >= target - freqTolerance:
                        power = diffPSD[peakIdx[-2]]
                        if power > max(1.5, 6 - 0.22 * target):
                            checkCCA = self._calculate_cca_score(differenceSig, samplingRate, targets, 1)
                            if checkCCA[0] == target:
                                return (target, secondPeakFreq)
                            else:
                                return None
                        else:
                            return None
            else:
                if secondPeakFreq <= target + freqTolerance and secondPeakFreq >= target - freqTolerance:
                    power = diffPSD[peakIdx[-2]]
                    if power > max(1.5, 6 - 0.22 * target):
                        checkCCA = self._calculate_cca_score(differenceSig, samplingRate, targets, 1)
                        if checkCCA[0] == target:
                            return (target, secondPeakFreq)
                        else:
                            return None
                    else:
                        return None

class MyOVBox(OVBox):
   
   def __init__(self):
      
        OVBox.__init__(self)
        
        self.signalHeader = None
        self.detector = None
        
        self.SamplingRate = None
        self.frequencies = None
        self.windowTime = None
        self.nTargets = None
        self.socket = None

   def initialize(self):
      
      self.SamplingRate = int(self.setting['Sampling rate'])
      self.windowTime = int(self.setting['Window time'])
      
      self.nTargets = int(self.setting['Number of targets'])
      
      self.targetFrequencies = np.zeros((self.nTargets,))
      for i in range(self.nTargets):
         self.targetFrequencies[i] = float(self.setting['Frequencies'].split(";")[i])
         
      self.detector = SsvepDetector()
      
      self.port = 5680
      self.ip = "localhost"
      
      self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      self.socket.connect((self.ip, self.port))
      print("CONNECTION ESTABLISHED")
      
   def uninitialize(self):
       
       self.socket.close()

   
   def process(self):

      for chunkIndex in range( len(self.input[0]) ):

         if(type(self.input[0][chunkIndex]) == OVSignalHeader):
            self.signalHeader = self.input[0].pop()
            outputHeader = OVSignalHeader(
            self.signalHeader.startTime,
            self.signalHeader.endTime,
            [1, self.signalHeader.dimensionSizes[1]],
            ['Oz']+self.signalHeader.dimensionSizes[1]*[''],
            self.signalHeader.samplingRate)

         elif(type(self.input[0][chunkIndex]) == OVSignalBuffer):
             
            chunk = self.input[0].pop()
            eegSignal = np.array(chunk).reshape(2, self.windowTime*self.SamplingRate)
            
            Oz = eegSignal[0, :]
            Fz = eegSignal[1, :]
            
            pred = self.detector.predict(Oz, Fz, self.SamplingRate, self.targetFrequencies)
            
            if pred != None:
               targetFreq = pred[0]
               peakFreq = pred[1]
               print(f"\nSSVEP detected at {targetFreq} Hz ({peakFreq} Hz). Sending message...")
               self.socket.sendall(str(targetFreq).split(".")[0].encode("utf-8"))
               print("Message sended...")
               
            else:
                print(f"\nRESTING")
         
box = MyOVBox()