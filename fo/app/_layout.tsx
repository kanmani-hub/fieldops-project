import { useCallback, useState } from "react";
import { Stack, type ErrorBoundaryProps } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { AnimatedLaunchScreen } from "../src/components/AnimatedLaunchScreen";

export default function RootLayout() {
  const [splashVisible, setSplashVisible] = useState(true);

  const handleSplashExit = useCallback(() => {
    setSplashVisible(false);
  }, []);

  return (
    <View style={styles.container}>
      <StatusBar style={splashVisible ? "light" : "dark"} />

      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: {
            backgroundColor: "#F4F8F6",
          },
        }}
      />

      {splashVisible && (
        <View style={styles.splashOverlay}>
          <AnimatedLaunchScreen
            readyToExit={true}
            onExitComplete={handleSplashExit}
          />
        </View>
      )}
    </View>
  );
}

export function ErrorBoundary({ error, retry }: ErrorBoundaryProps) {
  return (
    <View style={styles.errorScreen}>
      <StatusBar style="dark" />
      <Text style={styles.errorTitle}>FieldOps could not open this screen</Text>
      <Text style={styles.errorMessage}>{error.message || "An unexpected error occurred."}</Text>
      <Pressable accessibilityRole="button" onPress={retry} style={styles.retryButton}>
        <Text style={styles.retryText}>Try again</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  splashOverlay: {
    ...StyleSheet.absoluteFill,
    zIndex: 999,
    elevation: 999,
  },
  errorScreen: {
    flex: 1,
    justifyContent: "center",
    padding: 24,
    gap: 14,
    backgroundColor: "#F4F8F6",
  },
  errorTitle: {
    color: "#102A24",
    fontSize: 21,
    fontWeight: "800",
  },
  errorMessage: {
    color: "#52665F",
    fontSize: 13,
    lineHeight: 19,
  },
  retryButton: {
    alignSelf: "flex-start",
    marginTop: 4,
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 12,
    backgroundColor: "#006B4E",
  },
  retryText: {
    color: "#FFFFFF",
    fontSize: 14,
    fontWeight: "800",
  },
});
