pipeline {
    agent none
    options {
        skipStagesAfterUnstable()
        buildDiscarder logRotator(numToKeepStr: '10')
    }
    parameters {
        booleanParam defaultValue: true, description: 'Publish to Test PyPI', name: 'PublishTestPyPI'
    }
    stages {
        stage('Build') {
            agent {
                docker {
                    label 'linux'
                    image 'ghcr.io/astral-sh/uv:python3.13-bookworm'
                }
            }
            steps {
                 sh '''
                    git clean -fdx
                    python --version
                    pwd
                    df -h
                    ls -l
                    uv --version
                '''
                sh 'uv build --no-cache'
                stash(name: 'compiled-results', includes: 'dist/*.whl*')
            }
        }
        stage('Test') {
            agent {
                docker {
                    label 'linux'
                    image 'ghcr.io/astral-sh/uv:python3.13-bookworm'
                    args '-e HOME=/var/jenkins_home_tmp --tmpfs /var/jenkins_home_tmp:exec'
                }
            }
            steps {
                unstash(name: 'compiled-results')
                sh '''
                    uv pip install --no-cache \
                        --target ./test_install \
                        --extra-index-url https://test.pypi.org/simple \
                        --index-strategy unsafe-best-match \
                        dist/tstriage-0.1.$BUILD_NUMBER-py3-none-any.whl
                '''
            }
        }
        stage('Deploy') {
            when {
                expression {params.PublishTestPyPI == true}
            }
            agent {
                docker {
                    label 'linux'
                    image 'ghcr.io/astral-sh/uv:python3.13-bookworm'
                    args '-e HOME=/var/jenkins_home_tmp --tmpfs /var/jenkins_home_tmp:exec'
                }
            }
            steps {
                unstash(name: 'compiled-results')
                archiveArtifacts artifacts: 'dist/*.whl', fingerprint: true
                withCredentials([usernamePassword(credentialsId: '65ddf05a-75ed-43cd-ab7e-5ac1e6af2526', usernameVariable: 'USERNAME', passwordVariable: 'PASSWORD')]) {
                    sh 'uv publish --no-cache --publish-url https://test.pypi.org/legacy/ dist/* -u $USERNAME -p $PASSWORD'
                }
            }
        }
    }
    post {
        aborted {
            echo 'aborted'
            emailext subject: "ABORTED: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]'",
                body: """Job "${env.JOB_NAME} [${env.BUILD_NUMBER}]" aborted\nCheck console output at ${env.BUILD_URL}\n""",
                recipientProviders: [[$class: 'DevelopersRecipientProvider'], [$class: 'RequesterRecipientProvider']]
        }
        failure {
            echo 'failure'
            emailext subject: "FAILED: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]'",
                body: """Job "${env.JOB_NAME} [${env.BUILD_NUMBER}]" failed\nCheck console output at ${env.BUILD_URL}\n""",
                recipientProviders: [[$class: 'DevelopersRecipientProvider'], [$class: 'RequesterRecipientProvider']]
        }
        success {
            echo 'success'
            emailext subject: "SUCCEEDED: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]'",
                body: """Job "${env.JOB_NAME} [${env.BUILD_NUMBER}]" succeeded\nCheck console output at ${env.BUILD_URL}\n""",
                recipientProviders: [[$class: 'DevelopersRecipientProvider'], [$class: 'RequesterRecipientProvider']]
        }
        cleanup {
            echo 'Cleaning up ...'
        }
    }
}
